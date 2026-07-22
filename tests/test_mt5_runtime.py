from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from orderferry.runtime import (
    ConfigurationError,
    HistorySnapshotChangedError,
    Mt5Config,
    Mt5Runtime,
)


class FakeMt5:
    SOME_CONSTANT = 42

    def __init__(self, initialize_result: bool = True):
        self.initialize_result = initialize_result
        self.initialize_calls = []
        self.shutdown_calls = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self.current_bar_time = 1_700_000_000
        self.account_login = 1234

    def initialize(self, *args, **kwargs):
        self.initialize_calls.append((args, kwargs))
        return self.initialize_result

    def last_error(self):
        return (-6, "Terminal: Authorization failed")

    def terminal_info(self):
        if not self.initialize_result:
            return None
        return SimpleNamespace(name="Terminal", build=123, connected=True, maxbars=100_000)

    def copy_rates_from_pos(self, _symbol, _timeframe, start_pos, count):
        return [
            {"time": self.current_bar_time - (start_pos + offset) * 3600} for offset in range(count)
        ]

    def version(self):
        return (5, 0, 123)

    def account_info(self):
        return SimpleNamespace(
            login=self.account_login,
            server="Broker-Demo",
            balance=10.0,
            currency="USD",
        )

    def shutdown(self):
        self.shutdown_calls += 1

    def slow_call(self):
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        time.sleep(0.02)
        self.active_calls -= 1


class Mt5ConfigTests(unittest.TestCase):
    def test_parses_typed_environment(self):
        config = Mt5Config.from_environment(
            {
                "MT5_PATH": " terminal.exe ",
                "MT5_ACCOUNT": "1234",
                "MT5_PASSWORD": "secret",
                "MT5_SERVER": "Broker-Demo",
            }
        )

        self.assertEqual(config.login, 1234)
        self.assertEqual(
            config.initialize_call(),
            (
                ("terminal.exe",),
                {
                    "login": 1234,
                    "password": "secret",
                    "server": "Broker-Demo",
                },
            ),
        )

        configured = Mt5Config.from_environment({"ORDERFERRY_MINIMUM_TERMINAL_MAXBARS": "250000"})
        self.assertEqual(configured.minimum_terminal_maxbars, 250_000)

        overridden = Mt5Config.from_environment(
            {"ORDERFERRY_MINIMUM_TERMINAL_MAXBARS": "invalid"},
            minimum_terminal_maxbars=100_000,
        )
        self.assertEqual(overridden.minimum_terminal_maxbars, 100_000)

    def test_rejects_invalid_account_instead_of_silently_ignoring_it(self):
        with self.assertRaisesRegex(ConfigurationError, "must be an integer"):
            Mt5Config.from_environment({"MT5_ACCOUNT": "not-a-number"})


class Mt5RuntimeTests(unittest.TestCase):
    def test_failed_authorization_is_state_not_process_exit(self):
        runtime = Mt5Runtime(FakeMt5(initialize_result=False), Mt5Config())

        self.assertFalse(runtime.connect())
        self.assertFalse(runtime.connected)
        self.assertEqual(runtime.status.error_code, -6)
        self.assertEqual(
            runtime.status.error_message,
            "Terminal: Authorization failed",
        )

    def test_connection_lifecycle(self):
        module = FakeMt5()
        runtime = Mt5Runtime(module, Mt5Config(login=1234))

        self.assertTrue(runtime.connect())
        self.assertTrue(runtime.connected)
        self.assertEqual(module.initialize_calls, [((), {"login": 1234})])
        self.assertTrue(runtime.check_connection())

        runtime.disconnect()
        self.assertFalse(runtime.connected)
        self.assertEqual(module.shutdown_calls, 1)

    def test_all_extension_calls_share_one_lock(self):
        module = FakeMt5()
        runtime = Mt5Runtime(module, Mt5Config())
        threads = [threading.Thread(target=runtime.slow_call) for _ in range(4)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(module.max_active_calls, 1)
        self.assertEqual(runtime.SOME_CONSTANT, 42)

    def test_history_pages_share_a_stable_current_bar_anchor(self):
        module = FakeMt5()
        runtime = Mt5Runtime(module, Mt5Config())
        first = runtime.rates_page("XAUUSD", 16385, 0, 3, None)

        second = runtime.rates_page(
            "XAUUSD",
            16385,
            2,
            3,
            first["snapshot_id"],
        )
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertEqual(second["terminal_maxbars"], 100_000)
        self.assertEqual(second["account_login"], 1234)
        self.assertEqual(second["account_server"], "Broker-Demo")

        module.current_bar_time += 3600
        with self.assertRaises(HistorySnapshotChangedError):
            runtime.rates_page("XAUUSD", 16385, 4, 3, first["snapshot_id"])

        module.current_bar_time -= 3600
        module.account_login = 5678
        with self.assertRaises(HistorySnapshotChangedError):
            runtime.rates_page("XAUUSD", 16385, 4, 3, first["snapshot_id"])

    def test_history_page_rejects_a_rollover_inside_one_read(self):
        class RollingFakeMt5(FakeMt5):
            def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
                bars = super().copy_rates_from_pos(symbol, timeframe, start_pos, count)
                if count > 1:
                    self.current_bar_time += 3600
                return bars

        runtime = Mt5Runtime(RollingFakeMt5(), Mt5Config())

        with self.assertRaises(HistorySnapshotChangedError):
            runtime.rates_page("XAUUSD", 16385, 0, 3, None)


if __name__ == "__main__":
    unittest.main()
