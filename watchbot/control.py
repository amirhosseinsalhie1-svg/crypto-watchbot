"""Local UI queues commands; only the existing scanner writes trading state."""
from __future__ import annotations

import json
from pathlib import Path
import uuid
import time

from .core import Config, utc_ms


def queue_control(cfg: Config, action: str, expected_hash: str) -> dict:
    if action not in {"pause", "resume"}:
        raise ValueError("Unsupported control action")
    if expected_hash != cfg.fingerprint():
        raise ValueError("Experiment configuration mismatch")
    root = Path(cfg.database + ".controls")
    root.mkdir(parents=True, exist_ok=True)
    if len(list(root.glob("*.json"))) >= 32:
        raise ValueError("Too many queued commands; scanner must consume them first")
    item = {"id": uuid.uuid4().hex, "action": action, "requested_at": utc_ms(), "sequence": time.time_ns(),
            "config_hash": expected_hash}
    temporary = root / (item["id"] + ".tmp")
    with temporary.open("x", encoding="utf8") as out:
        json.dump(item, out)
    temporary.replace(root / (item["id"] + ".json"))
    return dict(item, status="QUEUED")


def consume_controls(journal, now: int):
    """Acknowledge atomically; replay and offline dashboards never call this."""
    root = Path(journal.cfg.database + ".controls")
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
        if path.stat().st_size > 4096:
            path.rename(path.with_suffix(".rejected"))
            continue
        try:
            item = json.loads(path.read_text(encoding="utf8"))
            ident = item["id"]
            action = item["action"]
            valid = (ident == path.stem and action in {"pause", "resume"}
                     and item["config_hash"] == journal.cfg.fingerprint()
                     and type(item["requested_at"]) is int
                     and 0 <= now - item["requested_at"] <= 120_000)
        except (ValueError, KeyError, TypeError):
            path.rename(path.with_suffix(".rejected"))
            continue
        with journal.transaction():
            # Crash after commit but before file removal must not apply again.
            seen = journal.get("control:" + ident)
            if not seen:
                if valid:
                    journal.put("paused", action == "pause")
                    if action == "pause":
                        journal.clear_pending()
                journal.event(now, "CONTROL_APPLIED" if valid else "CONTROL_REJECTED", item)
                journal.put("control:" + ident, True)
        path.unlink()
