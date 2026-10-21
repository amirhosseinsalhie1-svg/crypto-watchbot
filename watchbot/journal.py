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
    """Single writer, one process, bounded paper portfolio."""

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
        trade_columns = {row[1] for row in self.db.execute("PRAGMA table_info(trades)")}
        if "leverage" not in trade_columns:
            self.db.execute("ALTER TABLE trades ADD COLUMN leverage INTEGER NOT NULL DEFAULT 10")
        if "initial_margin" not in trade_columns:
            self.db.execute("ALTER TABLE trades ADD COLUMN initial_margin REAL NOT NULL DEFAULT 0")
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

    def _pending_orders(self):
        # `pending` remains readable for older dashboard clients and one-slot configs.
        return self.get("pending_orders", [self.get("pending")] if self.get("pending") else [])

    def _set_pending_orders(self, items):
        self.put("pending_orders", items)
        self.put("pending", items[0] if items else None)

    def clear_pending(self):
        self._set_pending_orders([])

    def schedule(self, a: Analysis, *, ignore_news: bool = False):
        self.schedule_many([a], ignore_news=ignore_news)

    def schedule_many(self, analyses, *, ignore_news: bool = False):
        """Rank same-scan candidates by estimated net R:R before reserving slots."""
        if not self.cfg.paper_enabled:
            return
        pending = self._pending_orders()
        opened = self.db.execute("SELECT COUNT(*) FROM trades WHERE exit_at IS NULL").fetchone()[0]
        occupied = opened + len(pending)
        candidates = []
        for a in analyses:
            if a.status != "TECHNICAL_CONFIRMED":
                continue
            if not entry_window_open(self.cfg, a.asof):
                self.event(a.asof, "PAPER_SESSION_BLOCK", {"id": a.signal_id})
                continue
            if not ignore_news and a.news_status != "CLEAR_REVIEWED":
                continue
            if any(x["symbol"] == a.symbol for x in pending):
                continue
            if self.db.execute("SELECT 1 FROM trades WHERE exit_at IS NULL AND symbol=?", (a.symbol,)).fetchone():
                continue
            if a.asof < self.get(f"last_signal:{a.symbol}", 0) + self.cfg.cooldown_bars * INTERVALS["5m"]:
                continue
            if not self.eligible(a.asof):
                continue
            entry = float(a.entry)
            direction = 1 if a.side == "LONG" else -1
            distance = direction * (entry - float(a.stop))
            reward = direction * (float(a.target) - entry)
            slip, fee = self.cfg.slippage_bps_per_side / 10_000, self.cfg.fee_bps_per_side / 10_000
            funding = entry * self.cfg.funding_bps_per_8h / 10_000 * self.cfg.max_hold_bars / 96
            risk = distance + entry * slip + fee * (entry + float(a.stop)) + funding
            net_reward = reward - entry * slip - fee * (entry + float(a.target)) - funding
            net_rr = net_reward / risk if risk > 0 else 0
            if self.cfg.min_net_reward_risk and net_rr + 1e-12 < self.cfg.min_net_reward_risk:
                self.event(a.asof, "PAPER_RR_BLOCKED", {"id": a.signal_id, "net_rr": net_rr,
                            "minimum": self.cfg.min_net_reward_risk, "stage": "signal_estimate"})
                continue
            candidates.append((net_rr, a))
        candidates.sort(key=lambda pair: (-pair[0], pair[1].symbol))
        for net_rr, a in candidates:
            if occupied >= self.cfg.max_open_positions:
                break
            pending.append(a.to_dict())
            occupied += 1
            self.put(f"last_signal:{a.symbol}", a.asof)
            self.event(a.asof, "PAPER_PENDING", {"id": a.signal_id, "ignore_news": ignore_news,
                                                  "estimated_net_rr": net_rr})
        self._set_pending_orders(pending)

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
            pending_orders = self._pending_orders()
            pending = next((p for p in pending_orders if p["symbol"] == symbol and at == p["asof"]), None)
            expired = [p for p in pending_orders if at > p["asof"]]
            for p in expired:
                self.event(at, "PAPER_EXPIRED", {"id": p["signal_id"]})
            pending_orders = [p for p in pending_orders if p not in expired and p is not pending]
            if pending:
                gate, _ = news_gate(self.cfg.news_file, symbol, at)
                if (not self.get("paused", False) and entry_window_open(self.cfg, at)
                        and (ignore_news or gate == "CLEAR_REVIEWED") and self.eligible(at)):
                    self._open(pending, row)
                else:
                    self.event(at, "PAPER_BLOCKED", {"news": gate})
            self._set_pending_orders(pending_orders)
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
        if (unit_risk / entry) >= 0.8 / cfg.paper_leverage:
            self.event(int(row.open_time), "PAPER_LEVERAGE_STOP_BUFFER_BLOCK", {
                "id": p["signal_id"], "leverage": cfg.paper_leverage,
                "risk_fraction_of_notional": unit_risk / entry,
                "maximum": 0.8 / cfg.paper_leverage})
            return
        target = entry + direction * cfg.reward_risk * distance
        unit_reward = direction * (target - entry) - entry * slip - fee * (entry + target) - funding_buffer
        net_rr = unit_reward / unit_risk if unit_risk > 0 else 0
        if cfg.min_net_reward_risk and net_rr + 1e-12 < cfg.min_net_reward_risk:
            self.event(int(row.open_time), "PAPER_RR_BLOCKED", {"id": p["signal_id"], "net_rr": net_rr,
                        "minimum": cfg.min_net_reward_risk, "stage": "fill_price"})
            return
        remaining_daily = max(0, self.get("day_start_equity") * cfg.daily_loss_fraction
                              - self.get("daily_gross_losses", 0))
        active_risk = self.db.execute('''SELECT COALESCE(SUM(r.planned_usdt),0) FROM trade_risk r
                              JOIN trades t ON t.id=r.trade_id WHERE t.exit_at IS NULL''').fetchone()[0]
        budget = min(equity * cfg.risk_fraction, max(0, remaining_daily - active_risk))
        qty = min(budget / unit_risk, equity * cfg.max_paper_notional_multiple / entry)
        if qty <= 0:
            return
        initial_margin = qty * entry / cfg.paper_leverage
        self.db.execute('''INSERT INTO trades(id,symbol,side,signal_at,entry_at,entry,stop,target,qty,leverage,initial_margin)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                        (p["signal_id"], p["symbol"], p["side"], p["asof"],
                         int(row.open_time), entry, stop, target, qty, cfg.paper_leverage, initial_margin))
        self.db.execute("INSERT INTO trade_risk VALUES(?,?)", (p["signal_id"], qty * unit_risk))
        states = self.get("position_states", {})
        states[p["signal_id"]] = {"id": p["signal_id"], "remaining_qty": qty,
                 "leverage": cfg.paper_leverage, "estimated_margin_usdt": initial_margin,
                 "current_stop": stop, "tp1": entry + direction * cfg.tp1_reward_risk * distance,
                 "tp1_done": False, "realized_pnl": 0}
        self.put("position_states", states)
        self.put("position_state", states[p["signal_id"]])
        self.event(int(row.open_time), "PAPER_OPEN", {"id": p["signal_id"], "qty": qty, "net_rr": net_rr})

    def _mark(self, p, row):
        direction = 1 if p["side"] == "LONG" else -1
        states = self.get("position_states", {})
        state = states.get(p["id"])
        if not state:
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
            states[p["id"]] = state
            self.put("position_states", states)
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
        states = self.get("position_states", {})
        if final:
            states.pop(p["id"], None)
        else:
            states[p["id"]] = state
        self.put("position_states", states)
        self.put("position_state", next(iter(states.values()), None))
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
                "pending": bool(self._pending_orders()),
                "pending_count": len(self._pending_orders()),
                "position_state": next(iter(self.get("position_states", {}).values()), self.get("position_state")),
                "position_states": list(self.get("position_states", {}).values()),
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
