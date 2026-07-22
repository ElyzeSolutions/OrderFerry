from __future__ import annotations

import unittest
from collections import namedtuple
from unittest.mock import Mock

import numpy as np

from orderferry.protocol import dispatch
from orderferry.runtime import HistorySnapshotChangedError


class ProtocolTests(unittest.TestCase):
    def test_ping_works_while_mt5_is_disconnected(self):
        mt5 = Mock(connected=False)

        self.assertEqual(dispatch(mt5, "__ping__", [], {}), {"ok": True, "result": "pong"})

    def test_capabilities_are_available_while_mt5_is_disconnected(self):
        mt5 = Mock(connected=False)

        result = dispatch(mt5, "__capabilities__", [], {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["protocol_version"], 2)
        self.assertEqual(result["result"]["history"]["max_page_bars"], 4096)

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

    def test_rates_page_is_bounded_and_returns_snapshot_metadata(self):
        mt5 = Mock(connected=True)
        mt5.rates_page.return_value = {
            "snapshot_id": "a" * 64,
            "terminal_maxbars": 100_000,
            "account_login": 1234,
            "account_server": "Broker-Demo",
            "bars": [{"time": 100}, {"time": 200}],
        }

        result = dispatch(
            mt5,
            "__rates_page__",
            [],
            {"symbol": "XAUUSD", "timeframe": 16385, "start_pos": 0, "count": 2},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["returned_count"], 2)
        self.assertEqual(result["result"]["terminal_maxbars"], 100_000)
        self.assertEqual(result["result"]["account_login"], 1234)
        mt5.rates_page.assert_called_once_with("XAUUSD", 16385, 0, 2, None)

        invalid = dispatch(
            mt5,
            "__rates_page__",
            [],
            {"symbol": "XAUUSD", "timeframe": 16385, "count": 4097},
        )
        self.assertEqual(invalid["error"]["code"], "INVALID_ARGS")

    def test_rates_page_serializes_real_mt5_structured_bars(self):
        mt5 = Mock(connected=True)
        dtype = [
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("spread", "i8"),
            ("real_volume", "i8"),
        ]
        mt5.rates_page.return_value = {
            "snapshot_id": "a" * 64,
            "terminal_maxbars": 100_000,
            "account_login": 1234,
            "account_server": "Broker-Demo",
            "bars": np.array(
                [(100, 2000.0, 2001.0, 1999.0, 2000.5, 10, 20, 0)],
                dtype=dtype,
            ),
        }

        result = dispatch(
            mt5,
            "__rates_page__",
            [],
            {"symbol": "XAUUSD", "timeframe": 16385, "start_pos": 0, "count": 1},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["bars"][0]["time"], 100)
        self.assertEqual(result["result"]["returned_count"], 1)

    def test_rates_page_reports_snapshot_rollover(self):
        mt5 = Mock(connected=True)
        mt5.rates_page.side_effect = HistorySnapshotChangedError("bar changed")

        result = dispatch(
            mt5,
            "__rates_page__",
            [],
            {
                "symbol": "XAUUSD",
                "timeframe": 16385,
                "start_pos": 4088,
                "count": 100,
                "snapshot_id": "a" * 64,
            },
        )

        self.assertEqual(result["error"]["code"], "HISTORY_SNAPSHOT_CHANGED")


if __name__ == "__main__":
    unittest.main()
