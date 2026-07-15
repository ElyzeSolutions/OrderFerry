from __future__ import annotations

import tempfile
import unittest
from unittest.mock import Mock, patch

from orderferry import app
from orderferry.protocol import dispatch


class ServiceStartupTests(unittest.TestCase):
    def test_transient_mt5_failure_keeps_bridge_running(self):
        runtime = Mock()
        runtime.connect.return_value = False
        server = Mock()

        with (
            tempfile.TemporaryDirectory() as log_dir,
            patch.object(
                app,
                "_runtime",
                return_value=runtime,
            ),
            patch.object(
                app,
                "BridgeServer",
                return_value=server,
            ),
        ):
            result = app.main(["--port", "18899", "--log-dir", log_dir])

        self.assertEqual(result, 0)
        runtime.connect.assert_not_called()
        server.start.assert_called_once_with()
        server.stop.assert_called_once_with()
        runtime.disconnect.assert_called_once_with()

    def test_self_test_still_reports_connection_failure(self):
        runtime = Mock()
        runtime.connect.return_value = False

        with (
            tempfile.TemporaryDirectory() as log_dir,
            patch.object(
                app,
                "_runtime",
                return_value=runtime,
            ),
        ):
            result = app.main(["--test", "--log-dir", log_dir])

        self.assertEqual(result, 1)

    def test_mt5_calls_fail_fast_while_watchdog_connects(self):
        runtime = Mock(connected=False)

        result = dispatch(runtime, "account_info", [], {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MT5_DISCONNECTED")
        runtime.account_info.assert_not_called()

    def test_login_remains_available_for_account_switching(self):
        runtime = Mock(connected=True)
        runtime.login.return_value = True

        result = dispatch(
            runtime,
            "login",
            [1234],
            {"password": "secret", "server": "Broker-Demo"},
        )

        self.assertEqual(result, {"ok": True, "result": True})
        runtime.login.assert_called_once_with(
            1234,
            password="secret",
            server="Broker-Demo",
        )


if __name__ == "__main__":
    unittest.main()
