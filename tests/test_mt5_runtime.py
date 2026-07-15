from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from orderferry.runtime import (
    ConfigurationError,
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

    def initialize(self, *args, **kwargs):
        self.initialize_calls.append((args, kwargs))
        return self.initialize_result

    def last_error(self):
        return (-6, "Terminal: Authorization failed")

    def terminal_info(self):
        if not self.initialize_result:
            return None
        return SimpleNamespace(name="Terminal", build=123, connected=True)

    def version(self):
        return (5, 0, 123)

    def account_info(self):
        return SimpleNamespace(login=1234, balance=10.0, currency="USD")

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


if __name__ == "__main__":
    unittest.main()
