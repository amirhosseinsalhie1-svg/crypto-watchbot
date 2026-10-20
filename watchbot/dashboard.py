"""Loopback-only evidence dashboard. No journal writes and no order endpoints."""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import urllib.parse
import zlib

from .core import Config, utc_ms
from .control import queue_control

WEB = Path(__file__).with_name("web")


def snapshot(cfg: Config, limit: int = 240) -> dict:
    now = utc_ms()
    result = {"schema_version": 1, "kind": "watchbot-dashboard-snapshot", "generated_at": now,
              "source": cfg.source, "symbols": list(cfg.symbols), "records": [], "trades": [],
              "events": [], "risk": {}, "health": {"status": "NO_JOURNAL", "last_success_ms": None},
              "config_hash": cfg.fingerprint(), "config_matches": True,
              "config": {k: v for k, v in asdict(cfg).items() if k not in {"database", "output_dir", "news_file"}}}
    path = Path(cfg.database)
    if not path.is_file():
        return result
    uri = "file:" + urllib.parse.quote(str(path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM meta")}
        result["config_matches"] = meta.get("config_hash") == cfg.fingerprint()
        result["config_hash"] = meta.get("config_hash")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Read under one SQLite snapshot; a concurrent scanner may keep writing.
        rows = db.execute("SELECT * FROM analyses ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        evidence = {}
        if "decision_evidence" in tables:
            ids = [r["id"] for r in rows]
            if ids:
                evidence = {r["analysis_id"]: r for r in db.execute(
                    "SELECT * FROM decision_evidence WHERE analysis_id IN (" + ",".join("?" for _ in ids) + ")", ids)}
        for row in reversed(rows):
            e = evidence.get(row["id"])
            result["records"].append({"id": row["id"], "analysis": json.loads(row["payload"]),
                "observed_at": e["observed_at"] if e else None,
                "frames": json.loads(zlib.decompress(e["frames"])) if e else {},
                "risk": json.loads(e["risk"]) if e else None,
                "evidence_status": "RECORDED" if e else "LEGACY_NO_INPUTS"})
        result["records"].sort(key=lambda r: (r["observed_at"] if r["observed_at"] is not None else r["analysis"]["asof"], r["id"]))
        result["trades"] = [dict(r) for r in db.execute("SELECT * FROM trades ORDER BY entry_at DESC LIMIT 500")]
        result["events"] = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 500")]
        result["risk"] = {"equity_usdt": meta.get("equity", cfg.equity), "paused": meta.get("paused", False),
            "paper_enabled": cfg.paper_enabled, "daily_gross_losses": meta.get("daily_gross_losses", 0),
            "day_start_equity": meta.get("day_start_equity", cfg.equity), "day": meta.get("day"),
            "consecutive_losses": meta.get("consecutive_losses", 0),
            "daily_loss_fraction": cfg.daily_loss_fraction, "risk_fraction": cfg.risk_fraction,
            "max_closed_equity_drawdown": meta.get("max_drawdown", 0),
            "pending": meta.get("pending") is not None,
            "open_positions": sum(t["exit_at"] is None for t in result["trades"])}
    result["health"]["status"] = "NO_HEARTBEAT"
    try:
        h = json.loads((Path(cfg.output_dir).parent / "heartbeat.json").read_text(encoding="utf8"))
        stamp = h["last_success_ms"]
        age = now - stamp
        result["health"] = dict(h, status="FRESH" if 0 <= age <= max(60_000, cfg.poll_seconds * 4000) else "STALE", age_ms=age)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result


def make_server(cfg: Config, port: int = 8765):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never echo user questions, headers or request bodies.

        def trusted(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return (host in hosts and (not origin or origin == "http://" + host)
                    and self.headers.get("Sec-Fetch-Site", "") not in {"cross-site"})

        def send_bytes(self, status, content, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, status, obj):
            self.send_bytes(status, json.dumps(obj, ensure_ascii=False, allow_nan=False).encode())

        def do_GET(self):
            if not self.trusted():
                return self.send_json(403, {"error": "Origin/host rejected"})
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/api/session":
                    return self.send_json(200, {"token": token, "mode": "LOCAL"})
                if path == "/api/snapshot":
                    return self.send_json(200, snapshot(cfg))
                assets = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
                          "/app.css": ("app.css", "text/css"), "/app.js": ("app.js", "text/javascript"),
                          "/model.js": ("model.js", "text/javascript")}
                if path in assets:
                    name, mime = assets[path]
                    return self.send_bytes(200, (WEB / name).read_bytes(), mime + "; charset=utf-8")
                return self.send_json(404, {"error": "Not found"})
            except (OSError, ValueError, sqlite3.Error, zlib.error):
                return self.send_json(503, {"error": "Journal temporarily unavailable or incompatible"})

        def do_POST(self):
            if (not self.trusted() or not secrets.compare_digest(self.headers.get("X-Watchbot-Token", ""), token)):
                return self.send_json(403, {"error": "Local session required"})
            if self.path != "/api/control":
                return self.send_json(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.send_json(400, {"error": "Invalid JSON request"})
                data = json.loads(self.rfile.read(length))
                if not Path(cfg.database).is_file() or not snapshot(cfg, limit=1)["config_matches"]:
                    return self.send_json(409, {"error": "No matching scanner journal"})
                return self.send_json(202, queue_control(cfg, data["action"], data["config_hash"]))
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
                return self.send_json(400, {"error": "Control request rejected"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(cfg: Config, port: int):
    with make_server(cfg, port) as server:
        print(f"WatchBot dashboard: http://127.0.0.1:{server.server_port} | local access only", flush=True)
        server.serve_forever()
