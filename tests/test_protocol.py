from __future__ import annotations

import unittest
from collections import namedtuple
from unittest.mock import Mock

from orderferry.protocol import dispatch


class ProtocolTests(unittest.TestCase):
    def test_ping_works_while_mt5_is_disconnected(self):
        mt5 = Mock(connected=False)

        self.assertEqual(dispatch(mt5, "__ping__", [], {}), {"ok": True, "result": "pong"})

    def test_disallowed_method_is_never_resolved(self):
        mt5 = Mock(connected=True)

        result = dispatch(mt5, "shutdown", [], {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_mt5_calls_fail_fast_while_watchdog_reconnects(self):
        mt5 = Mock(connected=False)

        result = dispatch(mt5, "account_info", [], {})

        self.assertEqual(result["error"]["code"], "MT5_DISCONNECTED")
        mt5.account_info.assert_not_called()

    def test_raw_order_send_requires_one_request_object(self):
        mt5 = Mock(connected=True)

        result = dispatch(mt5, "order_send", [{"symbol": "XAUUSD"}], {"request": {}})

        self.assertEqual(result["error"]["code"], "INVALID_ARGS")
        mt5.order_send.assert_not_called()

    def test_raw_order_send_maps_failed_trade_retcode(self):
        mt5 = Mock(connected=True)
        TradeResult = namedtuple("TradeResult", "retcode comment")
        mt5.order_send.return_value = TradeResult(10018, "closed")

        result = dispatch(mt5, "order_send", [{"symbol": "XAUUSD"}], {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MARKET_CLOSED")
        self.assertEqual(result["error"]["retcode"], 10018)


if __name__ == "__main__":
    unittest.main()
