"""OrderFerry command-line application and process lifecycle."""

from __future__ import annotations

import argparse
import logging
import os
import signal
from collections.abc import Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from orderferry import __version__
from orderferry.protocol import dispatch
from orderferry.runtime import ConfigurationError, Mt5Runtime
from orderferry.serialization import to_jsonable
from orderferry.server import BridgeServer

LOG_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
log = logging.getLogger("orderferry")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orderferry",
        description="Market-data and trade-execution relay for MetaTrader 5",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("ORDERFERRY_PORT", "18812")),
        help="TCP listen port (default: 18812)",
    )
    parser.add_argument(
        "--bind",
        default=os.getenv("ORDERFERRY_BIND", "127.0.0.1"),
        help="bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(
            os.getenv(
                "ORDERFERRY_LOG_DIR",
                Path.cwd() / "logs",
            )
        ),
        help="rotating log directory (default: ./logs)",
    )
    parser.add_argument(
        "--minimum-terminal-maxbars",
        type=int,
        default=None,
        help=(
            "minimum terminal history capacity reported in readiness "
            "(default: ORDERFERRY_MINIMUM_TERMINAL_MAXBARS or 100000)"
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="test MetaTrader connectivity and exit",
    )
    return parser


def _add_file_logging(log_dir: Path) -> RotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        (log_dir / "orderferry.log").resolve(),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def _remove_file_logging(handler: RotatingFileHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


def _runtime(minimum_terminal_maxbars: int | None) -> Mt5Runtime:
    return Mt5Runtime.from_environment(
        minimum_terminal_maxbars=minimum_terminal_maxbars,
    )


def _self_test(mt5: Mt5Runtime) -> bool:
    if not mt5.connect():
        return False
    try:
        print(f"Version: {mt5.version()}")
        account = to_jsonable(mt5.account_info())
        if account:
            print(
                f"Account: {account.get('login', '?')} | "
                f"Balance: {account.get('balance', '?')} {account.get('currency', '')}"
            )

        symbol = os.getenv("MT5_SYMBOL", "XAUUSD")
        tick = to_jsonable(mt5.symbol_info_tick(symbol))
        if tick:
            print(f"{symbol} tick: bid={tick.get('bid')} ask={tick.get('ask')}")
        else:
            print(f"{symbol}: no current tick")

        rates = to_jsonable(mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 3))
        if rates:
            print(f"Last {len(rates)} M15 bars ({symbol}):")
            for bar in rates:
                print(
                    f"  O={bar.get('open')} H={bar.get('high')} "
                    f"L={bar.get('low')} C={bar.get('close')}"
                )
        return True
    finally:
        mt5.disconnect()


def _serve(args: argparse.Namespace, mt5: Mt5Runtime) -> int:
    server = BridgeServer(mt5, dispatch, bind=args.bind, port=args.port)

    def shutdown(signum: int, _frame: Any) -> None:
        log.info("shutdown requested by signal %s", signum)
        server.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, shutdown)

    try:
        server.start()
    except KeyboardInterrupt:
        pass
    except OSError:
        log.exception("OrderFerry server failed")
        return 1
    finally:
        server.stop()
        mt5.disconnect()
        log.info("OrderFerry stopped")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = _parser().parse_args(argv)
    if args.minimum_terminal_maxbars is not None and not (
        1_000 <= args.minimum_terminal_maxbars <= 10_000_000
    ):
        _parser().error("--minimum-terminal-maxbars must be between 1000 and 10000000")
    handler = _add_file_logging(args.log_dir)
    try:
        log.info("OrderFerry %s logging to %s", __version__, handler.baseFilename)
        try:
            mt5 = _runtime(args.minimum_terminal_maxbars)
        except ConfigurationError as exc:
            log.error("invalid configuration: %s", exc)
            return 2
        return (0 if _self_test(mt5) else 1) if args.test else _serve(args, mt5)
    finally:
        _remove_file_logging(handler)
