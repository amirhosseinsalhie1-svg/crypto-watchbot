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


def request_json(url, *, data=None, headers=None, attempts=3, direct=False):
    """Bounded retries; errors never expose URLs (Telegram tokens live in URLs)."""
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            handlers = [NoRedirect()]
            if direct:
                handlers.append(urllib.request.ProxyHandler({}))
            with urllib.request.build_opener(*handlers).open(req, timeout=20) as response:
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


class ToobitReference(BinanceReference):
    """Public USDT perpetual feed; direct REST polling, no account API."""
    BASE = "https://api.toobit.com"

    def get(self, endpoint, **params):
        if endpoint == "/fapi/v1/time":
            path = "/api/v1/time"
        elif endpoint == "/fapi/v1/klines":
            path = "/quote/v1/klines"
            symbol = params["symbol"]
            params["symbol"] = symbol[:-4] + "-SWAP-USDT"
        else:
            raise ValueError("Unsupported Toobit market endpoint")
        payload = request_json(self.BASE + path + "?" + urllib.parse.urlencode(params), direct=True)
        if endpoint.endswith("/klines"):
            if not isinstance(payload, list):
                raise NetworkError("Invalid Toobit candle response")
            duration = INTERVALS[params["interval"]]
            normalized = []
            for row in payload:
                if not isinstance(row, list) or len(row) < 7:
                    raise NetworkError("Malformed Toobit candle")
                candle = row.copy()
                # Live API returns 0 for close time; derive the exclusive boundary - 1.
                opened = int(candle[0])
                if float(candle[0]) != opened:
                    raise NetworkError("Invalid Toobit timestamp")
                candle[6] = opened + duration - 1
                normalized.append(candle)
            return normalized
        if not isinstance(payload, dict) or "serverTime" not in payload:
            raise NetworkError("Invalid Toobit server time response")
        return payload

    def available_symbols(self):
        info = request_json(self.BASE + "/api/v1/exchangeInfo", direct=True)
        if not isinstance(info, dict) or not isinstance(info.get("contracts"), list):
            raise NetworkError("Invalid Toobit contract information")
        return {c["symbol"].replace("-SWAP-", "") for c in info["contracts"]
                if c.get("status") == "TRADING" and c.get("inverse") is False
                and c.get("marginToken") == "USDT" and c.get("quoteAsset") == "USDT"
                and c.get("symbol", "").endswith("-SWAP-USDT")}

    async def listen(self, symbols, trigger):
        while True:
            await asyncio.sleep(self.cfg.poll_seconds)
            trigger.set()


class MexcReference:
    """Public USDT perpetual candles via direct HTTPS; REST polling only."""
    BASE = "https://api.mexc.com"
    PERIODS = {"5m": "Min5", "4h": "Hour4", "1d": "Day1"}

    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = {}

    def get(self, endpoint, **params):
        payload = request_json(self.BASE + endpoint + "?" + urllib.parse.urlencode(params),
                               direct=True)
        if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != 0:
            raise NetworkError("MEXC public API rejected request")
        return payload["data"]

    def server_time(self):
        now = int(self.get("/api/v1/contract/ping"))
        if abs(now - utc_ms()) > 10_000:
            raise NetworkError("Host clock skew exceeds 10 seconds; synchronize clock")
        return now

    def available_symbols(self):
        rows = self.get("/api/v1/contract/detail/country")
        if isinstance(rows, dict):
            rows = [rows]
        return {r["symbol"].replace("_", "") for r in rows
                if r.get("state") == 0 and r.get("futureType") == 1
                and r.get("quoteCoin") == "USDT" and r.get("settleCoin") == "USDT"}

    def snapshot(self, symbols):
        now = self.server_time()
        result = {}
        for symbol in symbols:
            result[symbol] = {}
            for tf, duration in INTERVALS.items():
                boundary = now // duration * duration
                key = symbol, tf
                if key not in self.cache or self.cache[key][0] != boundary:
                    raw = self.get("/api/v1/contract/kline/" + symbol[:-4] + "_USDT",
                                   interval=self.PERIODS[tf],
                                   start=(boundary - self.cfg.history_bars * duration) // 1000,
                                   end=boundary // 1000 - 1)
                    keys = ("time", "open", "high", "low", "close", "vol")
                    if (not isinstance(raw, dict) or any(not isinstance(raw.get(k), list) for k in keys)
                            or len({len(raw[k]) for k in keys}) != 1):
                        raise NetworkError("Malformed MEXC candle arrays")
                    frame = pd.DataFrame({"open_time": raw["time"], "open": raw["open"],
                                          "high": raw["high"], "low": raw["low"],
                                          "close": raw["close"], "volume": raw["vol"]})
                    frame["open_time"] = pd.to_numeric(frame.open_time, errors="raise") * 1000
                    frame["close_time"] = frame.open_time + duration - 1
                    frame = frame[frame.open_time < boundary].reset_index(drop=True)
                    frame = validate_frame(frame, tf, now)
                    self.cache[key] = boundary, frame
                result[symbol][tf] = self.cache[key][1]
        return self.server_time(), result

    async def listen(self, symbols, trigger):
        while True:
            await asyncio.sleep(self.cfg.poll_seconds)
            trigger.set()


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
                    journal.put("pending", None)
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
