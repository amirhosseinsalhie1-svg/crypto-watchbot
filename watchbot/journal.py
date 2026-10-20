from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import sqlite3
import zlib
import math

from .core import Analysis, Config, INTERVALS, iso, news_gate, entry_window_open


def trading_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(
        ZoneInfo("Asia/Tehran")).date().isoformat()


class Journal:
    """Single writer, one process, one paper position across all symbols."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        Path(cfg.database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cfg.database)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS analyses(
                id INTEGER PRIMARY KEY, symbol TEXT, asof INTEGER, payload TEXT,
                UNIQUE(symbol,asof));
            CREATE TABLE IF NOT EXISTS trades(
                id TEXT PRIMARY KEY, symbol TEXT, side TEXT, signal_at INTEGER,
                entry_at INTEGER, exit_at INTEGER, entry REAL, stop REAL, target REAL,
                qty REAL, exit REAL, pnl REAL, reason TEXT, bars INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS outbox(
                id TEXT PRIMARY KEY, payload TEXT, photo TEXT, expires INTEGER,
                attempts INTEGER DEFAULT 0, due INTEGER DEFAULT 0,
                sent_at INTEGER, last_error TEXT);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, at INTEGER, kind TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS decision_evidence(
                analysis_id INTEGER PRIMARY KEY REFERENCES analyses(id),
                observed_at INTEGER NOT NULL, frames BLOB NOT NULL, risk TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_fills(
                id INTEGER PRIMARY KEY, trade_id TEXT, at INTEGER, qty REAL,
                price REAL, fees REAL, funding REAL, pnl REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS trade_risk(trade_id TEXT PRIMARY KEY, planned_usdt REAL);
        ''')
        existing = self.get("config_hash")
        if existing and existing != cfg.fingerprint():
            self.db.close()
            raise ValueError("Config changed: use a new database to keep experiments separate")
        if not existing:
            self.put("config_hash", cfg.fingerprint())
            self.put("equity", cfg.equity)
            self.put("peak_equity", cfg.equity)
            self.put("max_drawdown", 0)
            self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def event(self, at, kind, payload):
        self.db.execute("INSERT INTO events(at,kind,payload) VALUES(?,?,?)",
                        (at, kind, json.dumps(payload, ensure_ascii=False)))

    def day(self, at):
        day = trading_day(at)
        if self.get("day") != day:
            self.put("day", day)
            self.put("day_start_equity", self.get("equity"))
            self.put("daily_gross_losses", 0)
            self.put("consecutive_losses", 0)

    def eligible(self, at):
        self.day(at)
        return (self.get("equity") > 0
                and self.get("daily_gross_losses", 0) < self.get("day_start_equity") * self.cfg.daily_loss_fraction
                and self.get("consecutive_losses", 0) < 2)

    def record(self, a: Analysis):
        self.db.execute("INSERT OR IGNORE INTO analyses(symbol,asof,payload) VALUES(?,?,?)",
                        (a.symbol, a.asof, json.dumps(a.to_dict(), ensure_ascii=False)))

    def enqueue(self, a: Analysis, caption: str, photo: str):
        ident = a.signal_id or f"{a.symbol}:{a.asof}:{a.status}"
        self.db.execute("INSERT OR IGNORE INTO outbox(id,payload,photo,expires) VALUES(?,?,?,?)",
                        (ident, caption, photo, a.asof + INTERVALS["5m"]))

    def capture_evidence(self, a: Analysis, frames: dict, observed_at: int):
        """Immutable inputs known at this evaluation; legacy rows are not backfilled."""
        columns = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                   "ema50", "tenkan", "kijun", "span_a", "span_b", "atr14",
                   "body_ratio", "prior_volume20", "prior_high26", "prior_low26"]
        payload = {tf: [{key: (None if isinstance(value, float) and not math.isfinite(value) else value)
                        for key, value in row.items()}
                       for row in frame[[c for c in columns if c in frame]].to_dict(orient="records")]
                   for tf, frame in frames.items()}
        row = self.db.execute("SELECT id FROM analyses WHERE symbol=? AND asof=?", (a.symbol, a.asof)).fetchone()
        risk = dict(self.summary(), paused=self.get("paused", False), paper_enabled=self.cfg.paper_enabled,
                    day=self.get("day"), day_start_equity=self.get("day_start_equity", self.cfg.equity),
                    daily_gross_losses=self.get("daily_gross_losses", 0),
                    consecutive_losses=self.get("consecutive_losses", 0),
                    daily_loss_fraction=self.cfg.daily_loss_fraction, risk_fraction=self.cfg.risk_fraction)
        known = self.db.execute("SELECT id,exit_at FROM trades ORDER BY entry_at DESC LIMIT 500").fetchall()
        risk["known_trade_ids"] = [row["id"] for row in known]
        risk["known_closed_trade_ids"] = [row["id"] for row in known if row["exit_at"] is not None]
        self.db.execute("INSERT OR IGNORE INTO decision_evidence VALUES(?,?,?,?)", (
            row[0], int(observed_at), zlib.compress(json.dumps(payload, allow_nan=False).encode()),
            json.dumps(risk, ensure_ascii=False, allow_nan=False)))

    def schedule(self, a: Analysis, *, ignore_news: bool = False):
        if not self.cfg.paper_enabled or a.status != "TECHNICAL_CONFIRMED":
            return
        if not entry_window_open(self.cfg, a.asof):
            self.event(a.asof, "PAPER_SESSION_BLOCK", {"id": a.signal_id})
            return
        if not ignore_news and a.news_status != "CLEAR_REVIEWED":
            return
        if self.get("pending") or self.db.execute("SELECT 1 FROM trades WHERE exit_at IS NULL").fetchone():
            return
        if a.asof < self.get("last_signal", 0) + self.cfg.cooldown_bars * INTERVALS["5m"]:
            return
        if not self.eligible(a.asof):
            return
        self.put("pending", a.to_dict())
        self.put("last_signal", a.asof)
        self.event(a.asof, "PAPER_PENDING", {"id": a.signal_id, "ignore_news": ignore_news})

    def advance(self, symbol, bars, *, ignore_news=False):
        """Fill ONLY the next candle's open; process each completed bar exactly once."""
        if bars.empty:
            return
        cursor = self.get(f"cursor:{symbol}")
        # Startup does not invent fills for historical alerts.
        if cursor is None:
            self.put(f"cursor:{symbol}", int(bars.iloc[-1].close_time))
            return
        if cursor < int(bars.iloc[0].open_time) - 1:
            raise ValueError("Unrecoverable paper-history gap; fetch longer history or use a new experiment DB")
        for row in bars[bars.close_time > cursor].itertuples(index=False):
            at = int(row.open_time)
            self.day(at)
            pending = self.get("pending")
            if pending and at > pending["asof"]:
                self.event(at, "PAPER_EXPIRED", {"id": pending["signal_id"]})
                self.put("pending", None)
            if pending and pending["symbol"] == symbol and at == pending["asof"]:
                gate, _ = news_gate(self.cfg.news_file, symbol, at)
                self.put("pending", None)
                if (not self.get("paused", False) and entry_window_open(self.cfg, at)
                        and (ignore_news or gate == "CLEAR_REVIEWED") and self.eligible(at)):
                    self._open(pending, row)
                else:
                    self.event(at, "PAPER_BLOCKED", {"news": gate})
            position = self.db.execute("SELECT * FROM trades WHERE exit_at IS NULL AND symbol=?", (symbol,)).fetchone()
            if position:
                self._mark(position, row)
            self.put(f"cursor:{symbol}", int(row.close_time))

    def _open(self, p, row):
        cfg = self.cfg
        direction = 1 if p["side"] == "LONG" else -1
        if abs(row.open / p["entry"] - 1) > cfg.max_entry_gap_fraction:
            self.event(int(row.open_time), "PAPER_GAP_CANCEL", {"id": p["signal_id"]})
            return
        slip, fee = cfg.slippage_bps_per_side / 10_000, cfg.fee_bps_per_side / 10_000
        entry = float(row.open) * (1 + direction * slip)
        stop = p["stop"]
        distance = direction * (entry - stop)
        if not cfg.min_stop_fraction <= distance / entry <= cfg.max_stop_fraction:
            return
        equity = self.get("equity")
        funding_buffer = entry * cfg.funding_bps_per_8h / 10_000 * cfg.max_hold_bars / 96
        # Includes entry/stop fees, adverse stop slippage and a holding-cost buffer.
        unit_risk = distance + stop * slip + fee * (entry + stop * (1 - direction * slip)) + funding_buffer
        remaining_daily = max(0, self.get("day_start_equity") * cfg.daily_loss_fraction
                              - self.get("daily_gross_losses", 0))
        budget = min(equity * cfg.risk_fraction, remaining_daily)
        qty = min(budget / unit_risk, equity * cfg.max_paper_notional_multiple / entry)
        if qty <= 0:
            return
        target = entry + direction * cfg.reward_risk * distance
        self.db.execute('''INSERT INTO trades(id,symbol,side,signal_at,entry_at,entry,stop,target,qty)
                           VALUES(?,?,?,?,?,?,?,?,?)''',
                        (p["signal_id"], p["symbol"], p["side"], p["asof"],
                         int(row.open_time), entry, stop, target, qty))
        self.db.execute("INSERT INTO trade_risk VALUES(?,?)", (p["signal_id"], qty * unit_risk))
        self.put("position_state", {"id": p["signal_id"], "remaining_qty": qty,
                 "current_stop": stop, "tp1": entry + direction * cfg.tp1_reward_risk * distance,
                 "tp1_done": False, "realized_pnl": 0})
        self.event(int(row.open_time), "PAPER_OPEN", {"id": p["signal_id"], "qty": qty})

    def _mark(self, p, row):
        direction = 1 if p["side"] == "LONG" else -1
        state = self.get("position_state")
        if not state or state["id"] != p["id"]:
            state = {"id": p["id"], "remaining_qty": p["qty"], "current_stop": p["stop"],
                     "tp1_done": False, "realized_pnl": 0,
                     "tp1": p["entry"] + direction * self.cfg.tp1_reward_risk * abs(p["entry"] - p["stop"])}
        stop_hit = row.low <= state["current_stop"] if direction == 1 else row.high >= state["current_stop"]
        target_hit = row.high >= p["target"] if direction == 1 else row.low <= p["target"]
        count = p["bars"] + 1
        self.db.execute("UPDATE trades SET bars=? WHERE id=?", (count, p["id"]))
        if stop_hit:
            # Stop wins if both were touched; a gap through stop fills at worse open.
            raw_exit = min(row.open, state["current_stop"]) if direction == 1 else max(row.open, state["current_stop"])
            reason = "BE_STOP" if state["tp1_done"] else "STOP_AMBIGUOUS" if target_hit else "STOP"
            self._fill(p, row, state, state["remaining_qty"], raw_exit, reason, final=True)
            return
        tp1_hit = row.high >= state["tp1"] if direction == 1 else row.low <= state["tp1"]
        if self.cfg.tp1_close_fraction > 0 and not state["tp1_done"] and tp1_hit:
            self._fill(p, row, state, p["qty"] * self.cfg.tp1_close_fraction, state["tp1"], "TP1", final=False)
            state["tp1_done"], state["current_stop"] = True, p["entry"]
            # OHLC cannot reveal ordering. Conservatively assume the new stop was hit
            # after TP1 if both are in range; report this ambiguity explicitly.
            be_hit = row.low <= p["entry"] if direction == 1 else row.high >= p["entry"]
            if be_hit:
                self._fill(p, row, state, state["remaining_qty"], p["entry"], "BE_AMBIGUOUS_AFTER_TP1", final=True)
                return
        if target_hit:
            self._fill(p, row, state, state["remaining_qty"], p["target"], "TARGET", final=True)
        else:
            # A bar-count limit never liquidates a position. Continue managing it
            # on every finalized candle until the active stop or target is reached.
            self.put("position_state", state)

    def _fill(self, p, row, state, qty, raw_exit, reason, *, final):
        direction = 1 if p["side"] == "LONG" else -1
        exit_price = raw_exit * (1 - direction * self.cfg.slippage_bps_per_side / 10_000)
        # Conservatively charge elapsed time to candle close, even for intrabar exits.
        held_ms = int(row.close_time) + 1 - p["entry_at"]
        funding = p["entry"] * qty * self.cfg.funding_bps_per_8h / 10_000 * held_ms / 28_800_000
        fees = (p["entry"] + exit_price) * qty * self.cfg.fee_bps_per_side / 10_000
        pnl = direction * (exit_price - p["entry"]) * qty - fees - funding
        at = int(row.close_time) + 1
        self.db.execute("INSERT INTO trade_fills(trade_id,at,qty,price,fees,funding,pnl,reason) VALUES(?,?,?,?,?,?,?,?)",
                        (p["id"], at, qty, exit_price, fees, funding, pnl, reason))
        state["realized_pnl"] += pnl
        state["remaining_qty"] = max(0, state["remaining_qty"] - qty)
        if final:
            self.db.execute("UPDATE trades SET exit_at=?,exit=?,pnl=?,reason=? WHERE id=?",
                            (at, exit_price, state["realized_pnl"], reason, p["id"]))
        self.day(int(row.close_time) + 1)
        equity = self.get("equity") + pnl
        self.put("equity", equity)
        peak = max(self.get("peak_equity"), equity)
        self.put("peak_equity", peak)
        self.put("max_drawdown", max(self.get("max_drawdown"), 1 - equity / peak))
        if final:
            self.put("consecutive_losses", self.get("consecutive_losses", 0) + 1 if state["realized_pnl"] < 0 else 0)
        self.put("daily_gross_losses", self.get("daily_gross_losses", 0) + max(0, -pnl))
        self.put("position_state", None if final else state)
        self.event(at, "PAPER_CLOSED" if final else "PAPER_PARTIAL", {"id": p["id"], "pnl": pnl, "reason": reason})

    def summary(self):
        closed = self.db.execute("SELECT pnl FROM trades WHERE exit_at IS NOT NULL").fetchall()
        vals = [r[0] for r in closed]
        win, loss = sum(v for v in vals if v > 0), -sum(v for v in vals if v < 0)
        multiples = [r[0] for r in self.db.execute('''SELECT t.pnl/r.planned_usdt FROM trades t
                     JOIN trade_risk r ON r.trade_id=t.id WHERE t.exit_at IS NOT NULL AND r.planned_usdt>0''')]
        cost = self.db.execute("SELECT COALESCE(SUM(fees),0), COALESCE(SUM(funding),0) FROM trade_fills").fetchone()
        return {"mode": "PAPER_ONLY", "source": self.cfg.source,
                "config_hash": self.cfg.fingerprint(), "closed_trades": len(vals),
                "win_rate": sum(v > 0 for v in vals) / len(vals) if vals else None,
                "net_pnl_usdt": self.get("equity") - self.cfg.equity, "equity_usdt": self.get("equity"),
                "closed_trade_pnl_usdt": sum(vals),
                "expectancy_net_r_per_planned_risk": sum(multiples) / len(multiples) if multiples else None,
                "fees_usdt": cost[0], "funding_cost_estimate_usdt": cost[1],
                "profit_factor": win / loss if loss else None,
                "max_closed_equity_drawdown": self.get("max_drawdown"),
                "open_positions": self.db.execute("SELECT COUNT(*) FROM trades WHERE exit_at IS NULL").fetchone()[0],
                "pending": self.get("pending") is not None,
                "position_state": self.get("position_state"),
                "note": "Estimated costs, no mark-price liquidation model. No profitability conclusion."}

    def export(self, directory: str):
        import csv
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        for table in ["trades", "trade_fills", "trade_risk", "analyses", "events", "outbox"]:
            cursor = self.db.execute(f"SELECT * FROM {table}")
            with (folder / f"{table}.csv").open("w", newline="", encoding="utf-8-sig") as out:
                writer = csv.writer(out)
                writer.writerow([c[0] for c in cursor.description])
                writer.writerows(cursor)
        (folder / "summary.json").write_text(json.dumps(self.summary(), indent=2), encoding="utf8")
