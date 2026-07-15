from __future__ import annotations

import json
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from orderferry.runtime import Mt5ConnectionStatus
from orderferry.server import BridgeServer, ClientHandler


class FakeServer:
    def __init__(self):
        self.running = True
        self.mt5 = SimpleNamespace(connected=True)
        self.clients = set()

    def register_client(self, client):
        self.clients.add(client)

    def unregister_client(self, client):
        self.clients.discard(client)

    def dispatch(self, method, args, kwargs):
        if method == "__ping__":
            return {"ok": True, "result": "pong"}
        return {"ok": False, "error": {"code": "NOT_FOUND"}}


class ClientBoundaryTests(unittest.TestCase):
    def test_invalid_subscription_does_not_kill_connection(self):
        client_socket, server_socket = socket.socketpair()
        server = FakeServer()
        handler = ClientHandler(server_socket, ("local", 1), server)
        handler.start()

        reader = client_socket.makefile("r", encoding="utf-8")
        writer = client_socket.makefile("w", encoding="utf-8")
        try:
            writer.write(
                json.dumps(
                    {
                        "method": "__subscribe__",
                        "args": ["XAUUSD"],
                        "kwargs": {"interval_ms": "fast"},
                    }
                )
                + "\n"
            )
            writer.flush()
            invalid = json.loads(reader.readline())
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"]["code"], "INVALID_REQUEST")

            writer.write('{"method":"__ping__"}\n')
            writer.flush()
            self.assertEqual(json.loads(reader.readline())["result"], "pong")
        finally:
            server.running = False
            handler.stop()
            writer.close()
            reader.close()
            client_socket.close()
            handler.join(timeout=2.0)

        self.assertFalse(handler.is_alive())

    def test_subscription_mt5_failure_is_structured_and_connection_survives(self):
        client_socket, server_socket = socket.socketpair()
        server = FakeServer()
        server.mt5.symbol_select = Mock(side_effect=TimeoutError("locked"))
        handler = ClientHandler(server_socket, ("local", 1), server)
        handler.start()

        reader = client_socket.makefile("r", encoding="utf-8")
        writer = client_socket.makefile("w", encoding="utf-8")
        try:
            writer.write('{"id":7,"method":"__subscribe__","args":["XAUUSD"]}\n')
            writer.flush()
            failed = json.loads(reader.readline())
            self.assertEqual(failed["id"], 7)
            self.assertEqual(failed["error"]["code"], "MT5_BUSY")

            writer.write('{"method":"__ping__"}\n')
            writer.flush()
            self.assertEqual(json.loads(reader.readline())["result"], "pong")
        finally:
            server.running = False
            handler.stop()
            writer.close()
            reader.close()
            client_socket.close()
            handler.join(timeout=2.0)

        self.assertFalse(handler.is_alive())


class WatchdogTests(unittest.TestCase):
    def test_disconnected_runtime_is_retried_until_connected(self):
        class RecoveringRuntime:
            def __init__(self):
                self.status = Mt5ConnectionStatus(connected=False)
                self.connect_calls = 0
                self.connected_event = threading.Event()

            @property
            def connected(self):
                return self.status.connected

            def check_connection(self):
                raise AssertionError("disconnected runtimes should reconnect directly")

            def connect(self):
                self.connect_calls += 1
                self.status = Mt5ConnectionStatus(connected=True)
                self.connected_event.set()
                return True

        runtime = RecoveringRuntime()
        server = BridgeServer(
            runtime,
            dispatcher=lambda *_args, **_kwargs: {"ok": True},
            watchdog_interval=0.01,
        )
        watchdog = threading.Thread(target=server._watchdog)
        watchdog.start()
        try:
            self.assertTrue(runtime.connected_event.wait(timeout=1.0))
            self.assertTrue(runtime.connected)
            self.assertEqual(runtime.connect_calls, 1)
        finally:
            server.stop()
            watchdog.join(timeout=1.0)

        self.assertFalse(watchdog.is_alive())


if __name__ == "__main__":
    unittest.main()
