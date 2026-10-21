from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import os

from .core import Config, INTERVALS, analyze, utc_ms, validate_frame
from .diagnostics import validate_snapshot
from .control import consume_controls
from .io import BinanceReference, MexcReference, ToobitReference, NetworkError, Telegram, load_csv
from .journal import Journal
from .render import caption, chart


@contextmanager
def single_instance(database: str):
    """OS advisory lock; released on crashes, no stale pid-file deletion needed."""
    import sys
    path = Path(database + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError("Another process uses this experiment database") from None
    try:
        yield
    finally:
        stream.close()


class Engine:
    def __init__(self, cfg: Config):
        self.cfg, self.journal = cfg, Journal(cfg)

    def process(self, now: int, snapshots: dict, *, historical=False, ignore_news=False,
                render_all=False, queue_alerts=True, render_signals=True):
        j, cfg = self.journal, self.cfg
        prepared, reports = {}, []
        for symbol, frames in snapshots.items():
            # Validate all timeframes before advancing any paper state for this symbol.
            try:
                prepared[symbol] = {tf: validate_frame(frames[tf], tf, now, fresh=True)
                                    for tf in INTERVALS}
            except (KeyError, ValueError):
                pass
        with j.transaction():
            events = []
            for symbol, f in prepared.items():
                bars = f["5m"]
                cursor = j.get(f"cursor:{symbol}")
                if cursor is None:
                    j.advance(symbol, bars, ignore_news=ignore_news)
                    continue
                if cursor < int(bars.iloc[0].open_time) - 1:
                    raise ValueError("Paper history gap: scanner halted; restore full missing history")
                for index in bars.index[bars.close_time > cursor]:
                    events.append((int(bars.loc[index, "open_time"]), symbol, index))
            # Global equity and daily loss controls must see all symbols in time order.
            for _, symbol, index in sorted(events):
                j.advance(symbol, prepared[symbol]["5m"].loc[[index]], ignore_news=ignore_news)
            for symbol, frames in snapshots.items():
                a, f = analyze(symbol, frames, now, cfg, historical=historical)
                if ignore_news:
                    a.news_status = "UNSCREENED_RESEARCH"
                    a.news_note = "Replay explicitly omits fundamental screening"
                already = j.db.execute("SELECT 1 FROM analyses WHERE symbol=? AND asof=?",
                                       (a.symbol, a.asof)).fetchone()
                j.record(a)
                reports.append(a.to_dict())
                if already:
                    continue
                if not j.get("paused", False):
                    j.schedule(a, ignore_news=ignore_news)
                should_render = f and (render_all or (render_signals and a.status == "TECHNICAL_CONFIRMED"))
                if should_render:
                    photo = chart(a, f, cfg.output_dir)
                    Path(photo).with_suffix(".json").write_text(
                        json.dumps(a.to_dict(), ensure_ascii=False, indent=2), encoding="utf8")
                    Path(photo).with_suffix(".txt").write_text(caption(a), encoding="utf8")
                    if (queue_alerts and a.status == "TECHNICAL_CONFIRMED"
                            and not j.get("paused", False)):
                        last = j.get(f"alert:{symbol}", 0)
                        if a.asof >= last + cfg.cooldown_bars * INTERVALS["5m"]:
                            j.enqueue(a, caption(a), photo)
                            j.put(f"alert:{symbol}", a.asof)
                j.capture_evidence(a, f, now)
        return reports


async def run_live(cfg: Config, once=False):
    if cfg.source not in ("binance-usdm-reference", "mexc-usdt-perpetual", "toobit-usdt-perpetual"):
        raise ValueError("Unsupported live source; use Binance, MEXC, Toobit or CSV replay")
    provider = (ToobitReference(cfg) if cfg.source == "toobit-usdt-perpetual" else
                        MexcReference(cfg) if cfg.source == "mexc-usdt-perpetual" else BinanceReference(cfg))
    telegram = Telegram()
    available = await asyncio.to_thread(provider.available_symbols)
    missing = set(cfg.symbols) - available
    if missing:
        raise ValueError("Unavailable reference perpetual symbols: " + ", ".join(sorted(missing)))
    with single_instance(cfg.database):
        engine = Engine(cfg)
        trigger = asyncio.Event()
        task = asyncio.create_task(provider.listen(cfg.symbols, trigger)) if not once else None
        print("PAPER/RESEARCH ONLY | " + cfg.source + " | Telegram " +
              ("configured" if telegram.configured else "disabled (no credentials)"), flush=True)
        try:
            while True:
                try:
                    consume_controls(engine.journal, utc_ms())
                    # Commands commit before scan, so pause takes effect immediately.
                    try:
                        telegram.commands(engine.journal)
                        engine.journal.db.commit()
                    except NetworkError:
                        engine.journal.db.rollback()
                        print("TELEGRAM_UNAVAILABLE: market scanning continues", flush=True)
                    now, snapshots = await asyncio.to_thread(provider.snapshot, cfg.symbols)
                    # REST collection may cross a candle boundary after early frames
                    # were read. Do not label a partial/rejected scan as healthy.
                    validate_snapshot(cfg, now, snapshots)
                    reports = engine.process(now, snapshots, render_all=once)
                    print(json.dumps({"at": now, "statuses": {r["symbol"]: r["status"] for r in reports}},
                                     ensure_ascii=False), flush=True)
                    telegram.flush(engine.journal)
                    health_path = Path(cfg.output_dir).parent / "heartbeat.json"
                    health_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = health_path.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps({"last_success_ms": utc_ms(),
                        "snapshot_ms": now, "source": cfg.source,
                        "symbols": list(cfg.symbols)}), encoding="utf8")
                    temporary.replace(health_path)
                except (NetworkError, ValueError, OSError) as exc:
                    # Domain errors have sanitized messages; no traceback with secret URLs.
                    print(f"SCAN_PAUSED: {type(exc).__name__}: {exc}", flush=True)
                    engine.journal.db.rollback()
                    if once:
                        raise
                if once:
                    return
                try:
                    await asyncio.wait_for(trigger.wait(), timeout=cfg.poll_seconds)
                except asyncio.TimeoutError:
                    pass
                trigger.clear()
                await asyncio.sleep(1)  # REST finalization grace after a WS close event
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            engine.journal.close()


def replay(cfg: Config, folder: str, *, ignore_news=False):
    """Walk-forward on timestamped CSVs; no bars from the future enter indicators."""
    snapshots = {s: load_csv(folder, s) for s in cfg.symbols}
    for s, f in snapshots.items():
        for tf, d in f.items():
            validate_frame(d, tf, int(d.close_time.max()) + 1, fresh=False)
    timeline = sorted(set(int(v) + 1 for f in snapshots.values() for v in f["5m"].close_time.iloc[199:]))
    if not timeline:
        raise ValueError("No replay timeline")
    with single_instance(cfg.database):
        engine = Engine(cfg)
        try:
            if engine.journal.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]:
                raise ValueError("Replay requires a fresh experiment database")
            with engine.journal.transaction():
                engine.journal.event(timeline[0], "REPLAY_START", {"ignore_news": ignore_news,
                                                                "paper_enabled": cfg.paper_enabled})
            for now in timeline:
                sliced = {s: {tf: d[d.close_time < now].tail(cfg.history_bars)
                              for tf, d in f.items()} for s, f in snapshots.items()}
                engine.process(now, sliced, historical=True, ignore_news=ignore_news,
                               queue_alerts=False, render_signals=False)
            engine.journal.export(str(Path(cfg.output_dir).parent / "export"))
            return engine.journal.summary()
        finally:
            engine.journal.close()
