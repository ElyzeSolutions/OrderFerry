from __future__ import annotations

import unittest
from types import SimpleNamespace

from orderferry.trading import dispatch_trade


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_MODIFY = 7
    TRADE_ACTION_REMOVE = 8
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    POSITION_TYPE_BUY = 0

    def __init__(self):
        self.requests = []
        self.position_type = self.POSITION_TYPE_BUY

    def symbol_select(self, _symbol, _selected):
        return True

    def symbol_info(self, _symbol):
        return SimpleNamespace(filling_mode=2)

    def symbol_info_tick(self, _symbol):
        return SimpleNamespace(ask=3350.5, bid=3350.0)

    def order_send(self, request):
        self.requests.append(request)
        return SimpleNamespace(retcode=10009, order=10, comment="done")

    def positions_get(self, *, ticket=None, symbol=None):
        del symbol
        if ticket is None:
            return ()
        return (
            SimpleNamespace(
                ticket=ticket,
                symbol="XAUUSD",
                volume=0.2,
                type=self.position_type,
                magic=42,
                sl=0.0,
                tp=0.0,
            ),
        )


class TradingTests(unittest.TestCase):
    def test_buy_builds_market_request_from_current_ask(self):
        mt5 = FakeMt5()

        result = dispatch_trade(mt5, "buy", ["XAUUSD", 0.1], {"sl": 3300.0})

        self.assertTrue(result["ok"])
        request = mt5.requests[0]
        self.assertEqual(request["type"], mt5.ORDER_TYPE_BUY)
        self.assertEqual(request["price"], 3350.5)
        self.assertEqual(request["sl"], 3300.0)

    def test_close_buy_position_uses_sell_at_bid(self):
        mt5 = FakeMt5()

        result = dispatch_trade(mt5, "close_position", [123], {})

        self.assertTrue(result["ok"])
        request = mt5.requests[0]
        self.assertEqual(request["position"], 123)
        self.assertEqual(request["type"], mt5.ORDER_TYPE_SELL)
        self.assertEqual(request["price"], 3350.0)

    def test_pending_order_always_uses_return_filling(self):
        mt5 = FakeMt5()

        result = dispatch_trade(mt5, "buy_limit", ["XAUUSD", 0.1, 3300.0], {})

        self.assertTrue(result["ok"])
        self.assertEqual(mt5.requests[0]["type_filling"], mt5.ORDER_FILLING_RETURN)

    def test_missing_and_unknown_arguments_are_structured_errors(self):
        mt5 = FakeMt5()

        missing = dispatch_trade(mt5, "buy", ["XAUUSD"], {})
        unknown = dispatch_trade(mt5, "buy", ["XAUUSD", 0.1], {"surprise": True})

        self.assertEqual(missing["error"]["code"], "MISSING_ARGS")
        self.assertEqual(unknown["error"]["code"], "INVALID_ARGS")
        self.assertEqual(mt5.requests, [])

    def test_duplicate_positional_and_keyword_argument_is_rejected(self):
        mt5 = FakeMt5()

        result = dispatch_trade(mt5, "buy", ["XAUUSD", 0.1], {"volume": 0.2})

        self.assertEqual(result["error"]["code"], "INVALID_ARGS")
        self.assertIn("Multiple values", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
