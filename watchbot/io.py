from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import pandas as pd

from .core import Config, INTERVALS, utc_ms, validate_frame


class NetworkError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def request_json(url, *, data=None, headers=None, attempts=3):
    """Bounded retries; errors never expose URLs (Telegram tokens live in URLs)."""
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise NetworkError("Response exceeds size limit")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise NetworkError(f"HTTP {code}; request failed") from None
            delay = min(30, 2 ** attempt)
            if code == 429:
                try:
                    payload = json.loads(exc.read(8192))
                    delay = min(30, max(delay, int(payload.get("parameters", {}).get("retry_after", 5))))
                except (ValueError, TypeError):
                    pass
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == attempts - 1:
                raise NetworkError("Network or response error; details redacted") from None
            time.sleep(2 ** attempt)


class BinanceReference:
    """Public UM perpetual data ONLY. No API keys or order methods."""
    BASE = "https://fapi.binance.com"
    WS = "wss://fstream.binance.com/market/stream?streams="

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = {}

    def get(self, endpoint, **params):
        return request_json(self.BASE + endpoint + "?" + urllib.parse.urlencode(params))

    def available_symbols(self):
        info = self.get("/fapi/v1/exchangeInfo")
        return {s["symbol"] for s in info["symbols"]
                if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT" and s.get("marginAsset") == "USDT"}

    def snapshot(self, symbols):
        server = int(self.get("/fapi/v1/time")["serverTime"])
        if abs(server - utc_ms()) > 10_000:
            raise NetworkError("Host clock skew exceeds 10 seconds; synchronize clock")
        result = {}
        for symbol in symbols:
            result[symbol] = {}
            for tf, duration in INTERVALS.items():
                boundary = server // duration * duration
                key = symbol, tf
                cached = self.cache.get(key)
                if cached is None or cached[0] != boundary:
                    raw = self.get("/fapi/v1/klines", symbol=symbol, interval=tf,
                                   limit=self.cfg.history_bars + 1 if self.cfg.history_bars < 1000 else 1000,
                                   endTime=boundary - 1)
                    if not isinstance(raw, list):
                        raise NetworkError("Invalid candle response")
                    rows = [{"open_time": r[0], "open": r[1], "high": r[2], "low": r[3],
                             "close": r[4], "volume": r[5], "close_time": r[6]} for r in raw]
                    data = validate_frame(pd.DataFrame(rows), tf, server)
                    if int(data.iloc[-1].close_time) != boundary - 1:
                        raise NetworkError("Latest finalized candle is not yet available")
                    self.cache[key] = boundary, data
                result[symbol][tf] = self.cache[key][1]
        # Fetch a final timestamp: slow HTTP calls must not make old signals look fresh.
        finished = int(self.get("/fapi/v1/time")["serverTime"])
        if abs(finished - utc_ms()) > 10_000:
            raise NetworkError("Host clock skew exceeds 10 seconds; synchronize clock")
        return finished, result

    async def listen(self, symbols, trigger: asyncio.Event):
        from websockets.asyncio.client import connect
        from .market import MarketCache
        market = MarketCache(self.cfg)
        streams = "/".join(f"{s.lower()}@kline_5m" for s in symbols)
        delay = 1
        while True:
            try:
                async with connect(self.WS + streams, open_timeout=15, ping_interval=20,
                                   ping_timeout=20, max_size=1_000_000) as socket:
                    delay = 1
                    trigger.set()  # catch up through REST after every connection
                    async for message in socket:
                        payload = json.loads(message)
                        market.ingest(payload, utc_ms())
                        k = payload.get("data", {}).get("k", {})
                        if k.get("x") is True:
                            trigger.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Polling remains a fallback. Do not print connection URLs or payloads.
                trigger.set()
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)


class Telegram:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.operator = os.getenv("TELEGRAM_OPERATOR_USER_ID", "").strip()

    @property
    def configured(self):
        return bool(self.token and self.chat)

    def call(self, method, payload):
        result = request_json(f"https://api.telegram.org/bot{self.token}/{method}",
                              data=urllib.parse.urlencode(payload).encode(), attempts=1)
        if not result.get("ok"):
            raise NetworkError("Telegram rejected request; details redacted")
        return result["result"]

    def text(self, text):
        return self.call("sendMessage", {"chat_id": self.chat, "text": text[:4000]})

    def photo(self, path, caption):
        image = Path(path).read_bytes()
        if len(image) > 9_000_000:
            raise NetworkError("Chart exceeds photo size limit")
        boundary = "WatchBot" + uuid.uuid4().hex
        parts = []
        for key, value in {"chat_id": self.chat, "caption": caption[:1000]}.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                      'filename="analysis.png"\r\nContent-Type: image/png\r\n\r\n').encode()
                     + image + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        result = request_json(f"https://api.telegram.org/bot{self.token}/sendPhoto",
                              data=b"".join(parts),
                              headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, attempts=1)
        if not result.get("ok"):
            raise NetworkError("Telegram photo failed; details redacted")
        return result["result"]

    def commands(self, journal):
        if not self.configured:
            return
        updates = self.call("getUpdates", {"offset": journal.get("telegram_offset", 0),
                                           "timeout": 0, "limit": 50,
                                           "allowed_updates": '["message"]'})
        for update in updates:
            msg = update.get("message", {})
            chat = msg.get("chat", {})
            permitted = str(chat.get("id", "")) == self.chat
            if chat.get("type") != "private":
                permitted = permitted and bool(self.operator) and str(msg.get("from", {}).get("id")) == self.operator
            # Old commands are acknowledged but never acted on after a long outage.
            recent = 0 <= utc_ms() // 1000 - int(msg.get("date", 0)) < 120
            if permitted and recent:
                command = msg.get("text", "").split(" ")[0].split("@")[0]
                if command in ("/status", "/report"):
                    self.text(json.dumps(journal.summary(), ensure_ascii=False, indent=2))
                elif command == "/pause":
                    journal.put("paused", True)
                    journal.clear_pending()
                    self.text("هشدارها و ورود فرضی متوقف شد. معاملهٔ فرضی باز تا خروج پیگیری می‌شود.")
                elif command == "/resume":
                    journal.put("paused", False)
                    self.text("پایش آزمایشی از سر گرفته شد. هیچ معاملهٔ واقعی اجرا نمی‌شود.")
                elif command in ("/start", "/help"):
                    self.text("ربات پژوهشی و معاملات فرضی\n/status وضعیت\n/report گزارش\n/pause توقف\n/resume ادامه\nهیچ سفارشی به صرافی ارسال نمی‌شود.")
            journal.put("telegram_offset", int(update["update_id"]) + 1)

    def flush(self, journal):
        if not self.configured or journal.get("paused", False):
            return
        now = utc_ms()
        rows = journal.db.execute('''SELECT * FROM outbox WHERE sent_at IS NULL
                                  AND due<=? AND expires>? AND attempts<6 ORDER BY rowid LIMIT 4''', (now, now)).fetchall()
        for row in rows:
            try:
                self.photo(row["photo"], row["payload"])
                journal.db.execute("UPDATE outbox SET sent_at=?,last_error=NULL WHERE id=?", (utc_ms(), row["id"]))
            except (NetworkError, OSError):
                attempts = row["attempts"] + 1
                journal.db.execute("UPDATE outbox SET attempts=?,due=?,last_error=? WHERE id=?",
                                   (attempts, now + min(60, 2 ** attempts) * 1000,
                                    "Send failed; details redacted", row["id"]))
            journal.db.commit()


def load_csv(folder, symbol):
    return {tf: pd.read_csv(Path(folder) / f"{symbol}_{tf}.csv") for tf in INTERVALS}
