from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .core import Config, parse_time
from .demo import demo
from .diagnostics import doctor
from .engine import Engine, replay, run_live, single_instance
from .io import NetworkError, load_csv
from .journal import Journal


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crypto research alerts. No real order execution.")
    parser.add_argument("--config", default="config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("demo", help="Offline synthetic example; no Telegram messages")
    d.add_argument("--output", default="runtime/demo")
    health = sub.add_parser("doctor", help="Read-only setup checks; never sends Telegram messages")
    health.add_argument("--live", action="store_true", help="Also probe current public market data")
    ui = sub.add_parser("dashboard", help="Local evidence dashboard; reads the selected journal")
    ui.add_argument("--port", type=int, default=8765)
    bundle = sub.add_parser("dashboard-export", help="Export recorded evidence for the portable dashboard")
    bundle.add_argument("--output", default="runtime/dashboard-snapshot.json")
    sub.add_parser("run", help="Continuous WebSocket + REST scanner")
    sub.add_parser("market-feed", help="Read-only WebSocket chart feed; no REST bootstrap, analysis or Telegram")
    sub.add_parser("once", help="One live scan (sends alerts if Telegram is configured)")
    sub.add_parser("status", help="Read journal summary")
    e = sub.add_parser("export", help="Export journal CSVs and summary")
    e.add_argument("--output", default="runtime/export")
    r = sub.add_parser("replay", help="Walk-forward research over CSV files; no Telegram")
    r.add_argument("--data", required=True)
    r.add_argument("--ignore-news-for-research", action="store_true")
    history = sub.add_parser("history", help="Download and checksum-verify official Binance USD-M archives")
    history.add_argument("--start", required=True)
    history.add_argument("--end", required=True)
    history.add_argument("--output", required=True)
    bt = sub.add_parser("backtest", help="Limited walk-forward backtest over verified archive CSVs")
    bt.add_argument("--start", required=True)
    bt.add_argument("--end", required=True)
    bt.add_argument("--data", required=True)
    bt.add_argument("--output", required=True)
    bt.add_argument("--ignore-news-for-research", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = Config.load(args.config)
        if args.command == "history":
            from .archive import download_history
            result = download_history(cfg, parse_time(args.start), parse_time(args.end), args.output)
        elif args.command == "backtest":
            from .backtest import run_backtest
            result = run_backtest(cfg, args.data, parse_time(args.start), parse_time(args.end), args.output,
                                  ignore_news=args.ignore_news_for_research, progress=True)
        elif args.command == "dashboard":
            from .dashboard import serve
            serve(cfg, args.port)
            return 0
        elif args.command == "dashboard-export":
            from .dashboard import snapshot
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot(cfg), ensure_ascii=False, allow_nan=False), encoding="utf8")
            result = {"output": str(path.resolve())}
        elif args.command == "doctor":
            result = doctor(cfg, live=args.live)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        elif args.command == "demo":
            result = demo(cfg, args.output)
        elif args.command == "market-feed":
            from .io import BinanceReference
            if cfg.source != "binance-usdm-reference":
                raise ValueError("Live chart provider is Binance USD-M reference only")
            with single_instance(cfg.database):
                print("PRICE DISPLAY ONLY | no setup evaluation, paper fills, Telegram or orders", flush=True)
                asyncio.run(BinanceReference(cfg).listen(cfg.symbols, asyncio.Event()))
            return 0
        elif args.command in ("run", "once"):
            asyncio.run(run_live(cfg, once=args.command == "once"))
            return 0
        elif args.command == "replay":
            result = replay(cfg, args.data, ignore_news=args.ignore_news_for_research)
        else:
            with single_instance(cfg.database):
                j = Journal(cfg)
                try:
                    result = j.summary()
                    if args.command == "export":
                        j.export(args.output)
                finally:
                    j.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, NetworkError, RuntimeError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped by operator", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
