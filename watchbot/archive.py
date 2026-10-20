"""Checksum-verified Binance USD-M monthly OHLCV archives; public data only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.error
import urllib.request
from zipfile import ZipFile, BadZipFile

import pandas as pd

from .core import Config, INTERVALS, iso, utc_ms, validate_frame
from .io import NetworkError, NoRedirect

BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


def fetch_bytes(url: str, maximum: int) -> bytes:
    try:
        with urllib.request.build_opener(NoRedirect()).open(url, timeout=25) as response:
            value = response.read(maximum + 1)
        if len(value) > maximum:
            raise NetworkError("Archive exceeds download limit")
        return value
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetworkError("Official archive download failed; retry or check provider access") from exc


def month_keys(start: int, end: int) -> list[str]:
    dt = datetime.fromtimestamp(start / 1000, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = []
    while dt.timestamp() * 1000 < end:
        result.append(dt.strftime("%Y-%m"))
        dt = dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1)
    return result


def read_archive(blob: bytes, name: str) -> pd.DataFrame:
    try:
        with ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != name.replace(".zip", ".csv") or entries[0].file_size > 50_000_000:
                raise ValueError("Unexpected archive contents")
            raw = pd.read_csv(archive.open(entries[0]), header=None, dtype=str)
    except BadZipFile as exc:
        raise ValueError("Invalid ZIP archive") from exc
    if raw.shape[1] != 12:
        raise ValueError("Unexpected Binance OHLCV column count")
    # Futures archives may have a header. Do not drop numeric rows to repair data.
    if raw.iloc[0, 0] == "open_time":
        raw = raw.iloc[1:]
    d = raw.iloc[:, :7].copy()
    d.columns = COLUMNS
    return d.apply(pd.to_numeric, errors="raise").reset_index(drop=True)


def download_history(cfg: Config, start: int, end: int, output: str) -> dict:
    """Range is [start,end). Warm-up precedes start; incomplete months rejected."""
    if cfg.source != "binance-usdm-reference":
        raise ValueError("Archives are Binance USD-M reference data only")
    if not start < end or end - start > 31 * INTERVALS["1d"]:
        raise ValueError("Choose a limited historical range of at most 31 days")
    current_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if end > int(current_month.timestamp() * 1000):
        raise ValueError("Monthly archive range must end before the current UTC month")
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    cache = root / "archives"
    cache.mkdir(exist_ok=True)
    jobs = []
    for symbol in cfg.symbols:
        for tf, duration in INTERVALS.items():
            first = start - (cfg.history_bars + 2) * duration
            jobs.extend((symbol, tf, month) for month in month_keys(first, end))

    def obtain(job):
        symbol, tf, month = job
        name = f"{symbol}-{tf}-{month}.zip"
        url = f"{BASE}/{symbol}/{tf}/{name}"
        checksum = fetch_bytes(url + ".CHECKSUM", 4096).decode("ascii").split()
        if len(checksum) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum[0]) or checksum[1] != name:
            raise ValueError("Malformed official checksum")
        path = cache / name
        blob = path.read_bytes() if path.is_file() else fetch_bytes(url, 20_000_000)
        digest = hashlib.sha256(blob).hexdigest()
        if digest != checksum[0].lower():
            raise ValueError(f"SHA256 mismatch: {name}")
        if not path.is_file():
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(blob)
            temporary.replace(path)
        return job, read_archive(blob, name), {"url": url, "sha256": digest, "bytes": len(blob)}

    grouped = {(s, tf): [] for s in cfg.symbols for tf in INTERVALS}
    manifest = {"schema_version": 1, "source": cfg.source, "downloaded_at": iso(utc_ms()),
                "start_inclusive": iso(start), "end_exclusive": iso(end),
                "warmup_bars": cfg.history_bars, "archives": [], "files": []}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (symbol, tf, _), frame, evidence in pool.map(obtain, jobs):
            grouped[symbol, tf].append(frame)
            manifest["archives"].append(evidence)
    for (symbol, tf), frames in grouped.items():
        data = pd.concat(frames, ignore_index=True)
        data = validate_frame(data, tf, end, fresh=False)
        first = start - (cfg.history_bars + 2) * INTERVALS[tf]
        data = data[data.open_time >= first].reset_index(drop=True)
        validate_frame(data[data.close_time < start], tf, start, fresh=True, min_bars=cfg.history_bars)
        expected = end // INTERVALS[tf] * INTERVALS[tf] - 1
        if int(data.iloc[-1].close_time) != expected:
            raise ValueError(f"Archive does not cover the end boundary: {symbol}/{tf}")
        path = root / f"{symbol}_{tf}.csv"
        data.to_csv(path, index=False)
        manifest["files"].append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "rows": len(data), "first_open": iso(int(data.iloc[0].open_time)),
                                  "last_close": iso(int(data.iloc[-1].close_time) + 1)})
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    return manifest
