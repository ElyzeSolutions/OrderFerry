"""OrderFerry method allowlist and JSON protocol dispatch."""

from __future__ import annotations

import logging
from typing import Any

from orderferry.responses import Response, error, success
from orderferry.serialization import to_jsonable
from orderferry.trading import TRADE_METHODS, dispatch_trade, trade_response

log = logging.getLogger("orderferry.protocol")

_CONSTANT_NAMES = (
    "TIMEFRAME_M1",
    "TIMEFRAME_M2",
    "TIMEFRAME_M3",
    "TIMEFRAME_M4",
    "TIMEFRAME_M5",
    "TIMEFRAME_M6",
    "TIMEFRAME_M10",
    "TIMEFRAME_M12",
    "TIMEFRAME_M15",
    "TIMEFRAME_M20",
    "TIMEFRAME_M30",
    "TIMEFRAME_H1",
    "TIMEFRAME_H2",
    "TIMEFRAME_H3",
    "TIMEFRAME_H4",
    "TIMEFRAME_H6",
    "TIMEFRAME_H8",
    "TIMEFRAME_H12",
    "TIMEFRAME_D1",
    "TIMEFRAME_W1",
    "TIMEFRAME_MN1",
    "ORDER_TYPE_BUY",
    "ORDER_TYPE_SELL",
    "ORDER_TYPE_BUY_LIMIT",
    "ORDER_TYPE_SELL_LIMIT",
    "ORDER_TYPE_BUY_STOP",
    "ORDER_TYPE_SELL_STOP",
    "ORDER_TYPE_BUY_STOP_LIMIT",
    "ORDER_TYPE_SELL_STOP_LIMIT",
    "ORDER_TYPE_CLOSE_BY",
    "TRADE_ACTION_DEAL",
    "TRADE_ACTION_PENDING",
    "TRADE_ACTION_SLTP",
    "TRADE_ACTION_MODIFY",
    "TRADE_ACTION_REMOVE",
    "TRADE_ACTION_CLOSE_BY",
    "ORDER_FILLING_FOK",
    "ORDER_FILLING_IOC",
    "ORDER_FILLING_RETURN",
    "ORDER_TIME_GTC",
    "ORDER_TIME_DAY",
    "ORDER_TIME_SPECIFIED",
    "ORDER_TIME_SPECIFIED_DAY",
    "TRADE_RETCODE_DONE",
    "TRADE_RETCODE_PLACED",
    "TRADE_RETCODE_REQUOTE",
    "TRADE_RETCODE_REJECT",
    "TRADE_RETCODE_CANCEL",
    "TRADE_RETCODE_ERROR",
    "POSITION_TYPE_BUY",
    "POSITION_TYPE_SELL",
    "SYMBOL_TRADE_MODE_DISABLED",
    "SYMBOL_TRADE_MODE_FULL",
)

_RAW_METHODS = frozenset(
    {
        "login",
        "version",
        "terminal_info",
        "copy_rates_from_pos",
        "copy_rates_from",
        "copy_rates_range",
        "copy_ticks_from",
        "copy_ticks_range",
        "symbol_info",
        "symbol_info_tick",
        "symbol_select",
        "symbols_get",
        "symbols_total",
        "positions_get",
        "positions_total",
        "orders_get",
        "orders_total",
        "history_orders_get",
        "history_orders_total",
        "history_deals_get",
        "history_deals_total",
        "order_send",
        "order_check",
        "account_info",
        "last_error",
    }
)
ALLOWED_METHODS = _RAW_METHODS | TRADE_METHODS | frozenset({"__ping__", "__constants__"})


def _constants(mt5: Any) -> dict[str, int]:
    return {
        name: int(value)
        for name in _CONSTANT_NAMES
        if (value := getattr(mt5, name, None)) is not None
    }


def dispatch(
    mt5: Any,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any],
) -> Response:
    if method == "__ping__":
        return success("pong")
    if method == "__constants__":
        return success(_constants(mt5))
    if method not in ALLOWED_METHODS:
        return error(method, "METHOD_NOT_ALLOWED", f"Method not allowed: {method}")
    if not mt5.connected:
        return error(
            method,
            "MT5_DISCONNECTED",
            "MetaTrader is disconnected; the watchdog is reconnecting",
        )

    try:
        if method in TRADE_METHODS:
            return dispatch_trade(mt5, method, args, kwargs)

        function = getattr(mt5, method, None)
        if function is None:
            return error(method, "METHOD_NOT_FOUND", f"Method not found: {method}")

        if method in {"order_send", "order_check"}:
            if len(args) > 1 or set(kwargs) - {"request"}:
                return error(method, "INVALID_ARGS", f"{method} accepts one request object")
            if args and "request" in kwargs:
                return error(method, "INVALID_ARGS", "Multiple values for: request")
            request = kwargs.get("request", args[0] if args else {})
            if not isinstance(request, dict):
                return error(method, "INVALID_ARGS", "request must be an object")
            result = function(request)
            if method == "order_send":
                return trade_response(method, result, mt5)
            return success(to_jsonable(result))

        return success(to_jsonable(function(*args, **kwargs)))
    except TimeoutError as exc:
        log.error("MetaTrader lock timeout during %s: %s", method, exc)
        return error(method, "MT5_BUSY", f"MetaTrader is busy: {exc}")
    except Exception as exc:
        log.warning("dispatch failed for %s: %s", method, exc)
        return error(method, "EXCEPTION", f"{type(exc).__name__}: {exc}")
