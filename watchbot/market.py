"""Read-only, expendable live chart cache; never an execution or replay input."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .core import Config, INTERVALS, utc_ms


class MarketCache:
    def __init__(self, cfg: Config):
        self.cfg, self.symbols, self.last_write = cfg, {}, -1000

    def ingest(self, payload: dict, now: int) -> bool:
        try:
            event = payload.get("data", payload)
            k = event["k"]
            symbol, event_at = event["s"], int(event["E"])
            if symbol not in self.cfg.symbols or k["i"] != "5m" or k["s"] != symbol:
                return False
            bar = {"open_time": int(k["t"]), "close_time": int(k["T"]),
                   **{col: float(k[key]) for col, key in [("open", "o"), ("high", "h"),
                        ("low", "l"), ("close", "c"), ("volume", "v")]}}
            if (not all(math.isfinite(v) for v in bar.values()) or min(bar[c] for c in ["open", "high", "low", "close"]) <= 0
                    or bar["volume"] < 0 or bar["high"] < max(bar["open"], bar["low"], bar["close"])
                    or bar["low"] > min(bar["open"], bar["high"], bar["close"])
                    or bar["open_time"] % INTERVALS["5m"] or bar["close_time"] - bar["open_time"] != 299999
                    or not -10000 <= now - event_at <= 30000
                    or not bar["open_time"] <= event_at <= bar["close_time"] + 10000):
                return False
            previous = self.symbols.get(symbol)
            if previous and previous["event_at"] > event_at:
                return False
            self.symbols[symbol] = {"event_at": event_at, "observed_at": now, "bar": bar,
                                    "finalized": k.get("x") is True}
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            return False
        if now - self.last_write >= 1000 or k.get("x") is True:
            path = Path(self.cfg.output_dir).parent / "live-market.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"config_hash": self.cfg.fingerprint(), "source": self.cfg.source,
                "written_at": now, "symbols": self.symbols}, allow_nan=False), encoding="utf8")
            temporary.replace(path)
            self.last_write = now
        return True


def live_snapshot(cfg: Config, now: int | None = None) -> dict:
    now = utc_ms() if now is None else now
    result = {"source": cfg.source, "config_hash": cfg.fingerprint(), "server_at": now,
              "status": "UNAVAILABLE", "symbols": {}}
    path = Path(cfg.output_dir).parent / "live-market.json"
    try:
        if path.stat().st_size > 100000:
            return result
        data = json.loads(path.read_text(encoding="utf8"))
        if data["config_hash"] != cfg.fingerprint() or data["source"] != cfg.source:
            return result
        for symbol, record in data["symbols"].items():
            if symbol not in cfg.symbols:
                continue
            age = now - record["observed_at"]
            result["symbols"][symbol] = dict(record, age_ms=age,
                fresh=0 <= age <= 15000 and -10000 <= now - record["event_at"] <= 20000)
        result["status"] = "FRESH" if any(r["fresh"] for r in result["symbols"].values()) else "STALE"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return result
