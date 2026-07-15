"""High-level MetaTrader trade operations and structured trade results."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from orderferry.responses import Response, error, success
from orderferry.serialization import to_jsonable

log = logging.getLogger("orderferry.trading")

_RETCODES: dict[int, tuple[str, str]] = {
    10004: ("REQUOTE", "Requote"),
    10006: ("REJECT", "Request rejected"),
    10007: ("CANCEL", "Request cancelled by trader"),
    10008: ("PLACED", "Order placed"),
    10009: ("DONE", "Request executed"),
    10010: ("DONE_PARTIAL", "Request executed partially"),
    10011: ("ERROR", "Request processing error"),
    10012: ("TIMEOUT", "Request timed out"),
    10013: ("INVALID_REQUEST", "Invalid request"),
    10014: ("INVALID_VOLUME", "Invalid volume"),
    10015: ("INVALID_PRICE", "Invalid price"),
    10016: ("INVALID_STOPS", "Invalid stops (SL/TP)"),
    10017: ("TRADE_DISABLED", "Trade disabled"),
    10018: ("MARKET_CLOSED", "Market is closed"),
    10019: ("NO_MONEY", "Not enough money"),
    10020: ("PRICE_CHANGED", "Price changed"),
    10021: ("PRICE_OFF", "No quotes for processing"),
    10022: ("INVALID_EXPIRATION", "Invalid order expiration"),
    10023: ("ORDER_CHANGED", "Order state changed"),
    10024: ("TOO_MANY_REQUESTS", "Too many requests"),
    10025: ("NO_CHANGES", "No changes in request"),
    10026: ("AUTOTRADING_SERVER", "AutoTrading disabled by server"),
    10027: ("AUTOTRADING_DISABLED", "AutoTrading disabled in terminal"),
    10028: ("LOCKED", "Request locked for processing"),
    10029: ("FROZEN", "Order or position frozen"),
    10030: ("INVALID_FILL", "Invalid fill type"),
    10031: ("CONNECTION", "No connection to trade server"),
    10032: ("ONLY_REAL", "Operation allowed only for live accounts"),
    10033: ("LIMIT_ORDERS", "Pending orders limit reached"),
    10034: ("LIMIT_VOLUME", "Volume limit for symbol reached"),
    10035: ("INVALID_ORDER", "Invalid or prohibited order type"),
    10036: ("POSITION_CLOSED", "Position already closed"),
    10038: ("INVALID_CLOSE_VOLUME", "Close volume exceeds position volume"),
    10039: ("CLOSE_ORDER_EXISTS", "A close order already exists for the position"),
    10040: ("LIMIT_POSITIONS", "Open position limit reached"),
    10041: ("REJECT_CANCEL", "Pending order activation rejected and cancelled"),
    10042: ("LONG_ONLY", "Only long positions are allowed"),
    10043: ("SHORT_ONLY", "Only short positions are allowed"),
    10044: ("CLOSE_ONLY", "Only position closing is allowed"),
    10045: ("FIFO_CLOSE", "Positions must be closed in FIFO order"),
    10046: ("HEDGE_PROHIBITED", "Opposite positions are prohibited"),
}
_SUCCESS_RETCODES = frozenset({10008, 10009, 10010})


def trade_response(method: str, result: Any, mt5: Any) -> Response:
    normalized = to_jsonable(result)
    if normalized is None:
        try:
            last_error = mt5.last_error()
        except Exception:
            last_error = None
        log.warning("%s returned no result; last_error=%s", method, last_error)
        return error(
            method,
            "NO_RESULT",
            "MetaTrader returned no result",
            last_error=last_error,
        )

    retcode = normalized.get("retcode") if isinstance(normalized, dict) else None
    if retcode is None or retcode in _SUCCESS_RETCODES:
        return success(normalized)

    code, message = _RETCODES.get(retcode, ("UNKNOWN", f"Unknown error ({retcode})"))
    comment = normalized.get("comment")
    if comment:
        message = f"{message}: {comment}"
    response = error(method, code, message, retcode=retcode)
    response["result"] = normalized
    return response


def _filling_mode(mt5: Any, symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    if info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    if info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _market_order(
    mt5: Any,
    method: str,
    symbol: str,
    volume: float,
    *,
    sl: float = 0.0,
    tp: float = 0.0,
    deviation: int = 50,
    magic: int = 0,
    comment: str = "",
) -> Response:
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return error(method, "NO_TICK", f"No tick data for {symbol}")

    is_buy = method == "buy"
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if is_buy else tick.bid,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(mt5, symbol),
    }
    if sl:
        request["sl"] = sl
    if tp:
        request["tp"] = tp
    return trade_response(method, mt5.order_send(request), mt5)


def _buy(mt5: Any, symbol: str, volume: float, **options: Any) -> Response:
    return _market_order(mt5, "buy", symbol, volume, **options)


def _sell(mt5: Any, symbol: str, volume: float, **options: Any) -> Response:
    return _market_order(mt5, "sell", symbol, volume, **options)


def _pending_order(
    mt5: Any,
    method: str,
    symbol: str,
    volume: float,
    price: float,
    *,
    sl: float = 0.0,
    tp: float = 0.0,
    magic: int = 0,
    comment: str = "",
) -> Response:
    order_types = {
        "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
        "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
        "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
        "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
    }
    mt5.symbol_select(symbol, True)
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_types[method],
        "price": price,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    if sl:
        request["sl"] = sl
    if tp:
        request["tp"] = tp
    return trade_response(method, mt5.order_send(request), mt5)


def _close_position(mt5: Any, ticket: int, *, volume: float | None = None) -> Response:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return error("close_position", "NOT_FOUND", f"Position {ticket} not found")

    position = positions[0]
    mt5.symbol_select(position.symbol, True)
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        return error("close_position", "NO_TICK", f"No tick data for {position.symbol}")

    closes_buy = position.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume if volume is None else volume,
        "type": mt5.ORDER_TYPE_SELL if closes_buy else mt5.ORDER_TYPE_BUY,
        "position": ticket,
        "price": tick.bid if closes_buy else tick.ask,
        "deviation": 50,
        "magic": position.magic,
        "comment": "close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(mt5, position.symbol),
    }
    return trade_response("close_position", mt5.order_send(request), mt5)


def _close_all(mt5: Any, *, symbol: str | None = None) -> Response:
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if not positions:
        return success({"closed": 0, "failed": 0, "details": []})

    details = []
    closed = 0
    for position in positions:
        result = _close_position(mt5, position.ticket)
        closed += int(bool(result.get("ok")))
        details.append({"ticket": position.ticket, "symbol": position.symbol, **result})
    failed = len(details) - closed
    return {
        "ok": failed == 0,
        "result": {"closed": closed, "failed": failed, "details": details},
    }


def _modify_position(
    mt5: Any,
    ticket: int,
    *,
    sl: float | None = None,
    tp: float | None = None,
) -> Response:
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        return error("modify_position", "NOT_FOUND", f"Position {ticket} not found")
    position = positions[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": ticket,
        "sl": position.sl if sl is None else sl,
        "tp": position.tp if tp is None else tp,
    }
    return trade_response("modify_position", mt5.order_send(request), mt5)


def _modify_order(
    mt5: Any,
    ticket: int,
    *,
    price: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
) -> Response:
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        return error("modify_order", "NOT_FOUND", f"Order {ticket} not found")
    order = orders[0]
    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "symbol": order.symbol,
        "price": order.price_open if price is None else price,
        "sl": order.sl if sl is None else sl,
        "tp": order.tp if tp is None else tp,
        "type_time": order.type_time,
        "expiration": order.time_expiration,
    }
    return trade_response("modify_order", mt5.order_send(request), mt5)


def _cancel_order(mt5: Any, ticket: int) -> Response:
    request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    return trade_response("cancel_order", mt5.order_send(request), mt5)


@dataclass(frozen=True)
class Operation:
    handler: Callable[..., Response]
    positional: tuple[str, ...]
    optional: frozenset[str] = frozenset()


class ArgumentBindingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_OPERATIONS = {
    "buy": Operation(
        _buy, ("symbol", "volume"), frozenset({"sl", "tp", "deviation", "magic", "comment"})
    ),
    "sell": Operation(
        _sell, ("symbol", "volume"), frozenset({"sl", "tp", "deviation", "magic", "comment"})
    ),
    "close_position": Operation(_close_position, ("ticket",), frozenset({"volume"})),
    "close_all": Operation(_close_all, (), frozenset({"symbol"})),
    "modify_position": Operation(_modify_position, ("ticket",), frozenset({"sl", "tp"})),
    "modify_order": Operation(_modify_order, ("ticket",), frozenset({"price", "sl", "tp"})),
    "cancel_order": Operation(_cancel_order, ("ticket",)),
}
_PENDING_METHODS = frozenset({"buy_limit", "sell_limit", "buy_stop", "sell_stop"})
TRADE_METHODS = frozenset(_OPERATIONS) | _PENDING_METHODS


def _bind_arguments(
    method: str,
    positional_names: tuple[str, ...],
    optional_names: frozenset[str],
    args: list[Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if len(args) > len(positional_names):
        raise ArgumentBindingError(
            "INVALID_ARGS", f"{method} received too many positional arguments"
        )

    allowed = frozenset(positional_names) | optional_names
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise ArgumentBindingError("INVALID_ARGS", f"Unsupported arguments: {', '.join(unknown)}")

    duplicates = sorted(set(positional_names[: len(args)]) & set(kwargs))
    if duplicates:
        raise ArgumentBindingError("INVALID_ARGS", f"Multiple values for: {', '.join(duplicates)}")

    bound = dict(zip(positional_names, args, strict=False))
    for name in positional_names[len(args) :]:
        if name not in kwargs:
            raise ArgumentBindingError(
                "MISSING_ARGS", f"{method} requires {', '.join(positional_names)}"
            )
        bound[name] = kwargs[name]
    for name in optional_names:
        if name in kwargs:
            bound[name] = kwargs[name]
    return bound


def dispatch_trade(mt5: Any, method: str, args: list[Any], kwargs: dict[str, Any]) -> Response:
    try:
        if method in _PENDING_METHODS:
            bound = _bind_arguments(
                method,
                ("symbol", "volume", "price"),
                frozenset({"sl", "tp", "magic", "comment"}),
                args,
                kwargs,
            )
            return _pending_order(mt5, method, **bound)

        operation = _OPERATIONS.get(method)
        if operation is None:
            return error(method, "UNKNOWN_METHOD", f"Unknown trade method: {method}")
        bound = _bind_arguments(method, operation.positional, operation.optional, args, kwargs)
        return operation.handler(mt5, **bound)
    except ArgumentBindingError as exc:
        return error(method, exc.code, str(exc))
