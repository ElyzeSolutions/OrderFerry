"""TCP transport, client lifecycle, and MT5 connection supervision."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from orderferry.responses import Response, error, success
from orderferry.protocol import PROTOCOL_VERSION
from orderferry.runtime import Mt5Runtime
from orderferry.serialization import to_jsonable

log = logging.getLogger("orderferry.server")

Dispatcher = Callable[[Mt5Runtime, str, list[Any], dict[str, Any]], Response]

MAX_REQUEST_BYTES = 1_048_576
WATCHDOG_INTERVAL_SECONDS = 30.0


def _request_error(code: str, message: str) -> Response:
    return error("", code, message)


class TickSubscriber:
    """Manage tick subscriptions for one client connection."""

    def __init__(self, mt5: Mt5Runtime, send: Callable[[dict], None]):
        self.mt5 = mt5
        self._send = send
        self._symbols: dict[str, dict[str, float | None]] = {}
        self._interval_ms = 200
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def subscribe(
        self,
        symbols: list[str],
        interval_ms: int = 200,
    ) -> Response:
        for symbol in symbols:
            if not self.mt5.symbol_select(symbol, True):
                return error(
                    "__subscribe__",
                    "SYMBOL_NOT_AVAILABLE",
                    f"MetaTrader could not select symbol: {symbol}",
                )

        with self._lock:
            for symbol in symbols:
                self._symbols[symbol] = {"last_bid": None, "last_ask": None}

            self._interval_ms = max(50, interval_ms)
            if self._thread is None or not self._thread.is_alive():
                self._stop_event.clear()
                self._thread = threading.Thread(
                    target=self._poll_loop,
                    name="mt5-tick-subscriber",
                    daemon=True,
                )
                self._thread.start()

            subscribed = list(self._symbols)

        return success({"subscribed": subscribed, "interval_ms": self._interval_ms})

    def unsubscribe(self, symbols: list[str]) -> Response:
        removed = []
        with self._lock:
            for symbol in symbols:
                if self._symbols.pop(symbol, None) is not None:
                    removed.append(symbol)
            remaining = list(self._symbols)
            if not remaining:
                self._stop_event.set()

        return success({"unsubscribed": removed, "remaining": remaining})

    def stop(self) -> None:
        with self._lock:
            self._symbols.clear()
            self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._symbols)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                symbols = {symbol: state.copy() for symbol, state in self._symbols.items()}
                interval_seconds = self._interval_ms / 1000.0

            for symbol, state in symbols.items():
                if self._stop_event.is_set():
                    return
                try:
                    tick = self.mt5.symbol_info_tick(symbol)
                    if tick is None:
                        continue

                    bid, ask = float(tick.bid), float(tick.ask)
                    if bid == state["last_bid"] and ask == state["last_ask"]:
                        continue

                    with self._lock:
                        current = self._symbols.get(symbol)
                        if current is None:
                            continue
                        current["last_bid"] = bid
                        current["last_ask"] = ask

                    self._send(
                        {
                            "event": "tick",
                            "symbol": symbol,
                            "bid": bid,
                            "ask": ask,
                            "last": float(tick.last),
                            "volume": float(tick.volume),
                            "time": int(tick.time),
                            "time_msc": int(tick.time_msc),
                        }
                    )
                except (ConnectionResetError, BrokenPipeError, OSError):
                    self._stop_event.set()
                    return
                except Exception as exc:
                    log.debug("tick polling failed for %s: %s", symbol, exc)

            self._stop_event.wait(interval_seconds)


class ClientHandler(threading.Thread):
    def __init__(
        self,
        conn: socket.socket,
        addr: tuple[str, int],
        server: "BridgeServer",
    ):
        super().__init__(name=f"client-{addr[0]}:{addr[1]}", daemon=True)
        self.conn = conn
        self.addr = addr
        self.server = server
        self._subscriber: TickSubscriber | None = None
        self._send_lock = threading.Lock()

    def run(self) -> None:
        log.info("client connected: %s:%d", *self.addr)
        self.server.register_client(self)
        buffer = b""
        try:
            while self.server.running:
                data = self.conn.recv(262_144)
                if not data:
                    break
                buffer += data
                if len(buffer) > MAX_REQUEST_BYTES and b"\n" not in buffer:
                    self._send(
                        _request_error(
                            "REQUEST_TOO_LARGE",
                            f"request exceeds {MAX_REQUEST_BYTES} bytes",
                        )
                    )
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > MAX_REQUEST_BYTES:
                        self._send(
                            _request_error(
                                "REQUEST_TOO_LARGE",
                                f"request exceeds {MAX_REQUEST_BYTES} bytes",
                            )
                        )
                    elif line.strip():
                        self._handle_line(line)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        except Exception:
            log.exception("unexpected client handler failure for %s:%d", *self.addr)
        finally:
            if self._subscriber:
                self._subscriber.stop()
                self._subscriber.join(5.0)
            self.server.unregister_client(self)
            self._close_socket()
            log.info("client disconnected: %s:%d", *self.addr)

    def stop(self) -> None:
        if self._subscriber:
            self._subscriber.stop()
        self._close_socket()

    def _close_socket(self) -> None:
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.conn.close()
        except OSError:
            pass

    def _handle_line(self, line: bytes) -> None:
        try:
            request = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send(_request_error("JSON_DECODE", str(exc)))
            return

        if not isinstance(request, dict):
            self._send(_request_error("INVALID_REQUEST", "request must be an object"))
            return

        method = request.get("method")
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})
        request_id = request.get("id")
        if not isinstance(method, str) or not method:
            self._send(_request_error("INVALID_REQUEST", "method must be a non-empty string"))
            return
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            self._send(
                _request_error("INVALID_REQUEST", "args must be a list and kwargs an object")
            )
            return

        started = time.monotonic()
        try:
            result = self._dispatch_method(method, args, kwargs)
        except TimeoutError as exc:
            result = error(method, "MT5_BUSY", f"MetaTrader is busy: {exc}")
        except Exception as exc:
            log.warning("client request failed for %s: %s", method, exc)
            result = error(method, "EXCEPTION", f"{type(exc).__name__}: {exc}")

        elapsed_ms = (time.monotonic() - started) * 1000
        if request_id is not None:
            result["id"] = request_id
        if method != "__ping__":
            level = logging.DEBUG if method in ("__constants__", "__status__") else logging.INFO
            log.log(
                level,
                "%s %s -> ok=%s (%.1fms)",
                self.name,
                method,
                result.get("ok"),
                elapsed_ms,
            )
        self._send(result)

    def _dispatch_method(
        self,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Response:
        if method == "__subscribe__":
            if not self.server.mt5.connected:
                return error(
                    method,
                    "MT5_DISCONNECTED",
                    "MetaTrader is disconnected; the watchdog is reconnecting",
                )
            if not args or not all(isinstance(symbol, str) and symbol for symbol in args):
                return error(
                    method,
                    "INVALID_REQUEST",
                    "subscription symbols must be non-empty strings",
                )
            interval_ms = kwargs.get("interval_ms", 200)
            if not isinstance(interval_ms, int) or isinstance(interval_ms, bool):
                return error(method, "INVALID_REQUEST", "interval_ms must be an integer")
            if set(kwargs) - {"interval_ms"}:
                return error(
                    method,
                    "INVALID_REQUEST",
                    "unsupported subscription options",
                )
            if self._subscriber is None:
                self._subscriber = TickSubscriber(self.server.mt5, self._send)
            return self._subscriber.subscribe(args, interval_ms=interval_ms)

        if method == "__unsubscribe__":
            if not all(isinstance(symbol, str) and symbol for symbol in args):
                return error(
                    method,
                    "INVALID_REQUEST",
                    "subscription symbols must be strings",
                )
            if kwargs:
                return error(method, "INVALID_REQUEST", "unsubscribe accepts no options")
            if self._subscriber is None:
                return success({"unsubscribed": [], "remaining": []})
            return self._subscriber.unsubscribe(args)

        return self.server.dispatch(method, args, kwargs)

    def _send(self, payload: dict) -> None:
        try:
            data = (json.dumps(payload, default=str) + "\n").encode("utf-8")
            with self._send_lock:
                self.conn.sendall(data)
        except (ConnectionResetError, BrokenPipeError, OSError):
            raise
        except (TypeError, ValueError) as exc:
            log.warning("could not serialize response for %s: %s", self.name, exc)


class BridgeServer:
    def __init__(
        self,
        mt5: Mt5Runtime,
        dispatcher: Dispatcher,
        *,
        bind: str = "127.0.0.1",
        port: int = 18812,
        watchdog_interval: float = WATCHDOG_INTERVAL_SECONDS,
    ):
        self.mt5 = mt5
        self.bind = bind
        self.port = port
        self._dispatcher = dispatcher
        self._watchdog_interval = watchdog_interval
        self._running = False
        self._socket: socket.socket | None = None
        self._started_at = time.time()
        self._clients: set[ClientHandler] = set()
        self._clients_lock = threading.Lock()
        self._watchdog_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    def register_client(self, client: ClientHandler) -> None:
        with self._clients_lock:
            self._clients.add(client)

    def unregister_client(self, client: ClientHandler) -> None:
        with self._clients_lock:
            self._clients.discard(client)

    def dispatch(
        self,
        method: str,
        args: list,
        kwargs: dict,
    ) -> dict[str, Any]:
        if method == "__status__":
            return success(self.get_status())
        return self._dispatcher(self.mt5, method, args, kwargs)

    def get_status(self) -> dict[str, Any]:
        runtime_status = self.mt5.status
        connected = runtime_status.connected
        info: dict[str, Any] = {
            "connected": connected,
            "protocol_version": PROTOCOL_VERSION,
            "uptime_s": round(time.time() - self._started_at),
        }
        terminal_maxbars = None
        if runtime_status.error_message:
            info["connection_error"] = {
                "code": runtime_status.error_code,
                "message": runtime_status.error_message,
            }
        if connected:
            try:
                terminal = self.mt5.terminal_info()
                if terminal:
                    normalized = to_jsonable(terminal)
                    info["mt5_build"] = normalized.get("build")
                    info["mt5_connected"] = normalized.get("connected", False)
                    terminal_maxbars = normalized.get("maxbars")
            except Exception as exc:
                log.debug("could not read terminal status: %s", exc)

            try:
                account = self.mt5.account_info()
                if account:
                    normalized = to_jsonable(account)
                    info.update(
                        {
                            "account": normalized.get("login"),
                            "balance": normalized.get("balance"),
                            "equity": normalized.get("equity"),
                            "currency": normalized.get("currency"),
                        }
                    )
            except Exception as exc:
                log.debug("could not read account status: %s", exc)

        required_maxbars = self.mt5.config.minimum_terminal_maxbars
        maxbars_satisfied = (
            isinstance(terminal_maxbars, int) and terminal_maxbars >= required_maxbars
        )
        info["terminal_history"] = {
            "actual_maxbars": terminal_maxbars,
            "minimum_maxbars": required_maxbars,
            "satisfied": maxbars_satisfied,
        }
        info["ready_for_history"] = connected and maxbars_satisfied

        with self._clients_lock:
            info["clients"] = len(self._clients)
            info["subscriptions"] = sum(
                client._subscriber.active_count for client in self._clients if client._subscriber
            )
        return info

    def _watchdog(self) -> None:
        while not self._stop_event.is_set():
            was_connected = self.mt5.connected
            if not was_connected or not self.mt5.check_connection():
                if was_connected:
                    log.warning("MT5 connection lost")
                log.info("attempting MT5 reconnect")
                self.mt5.connect()
            if self._stop_event.wait(self._watchdog_interval):
                return

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.settimeout(2.0)
            listener.bind((self.bind, self.port))
            listener.listen(8)
        except Exception:
            listener.close()
            raise

        self._socket = listener
        self._running = True
        self._started_at = time.time()
        self._stop_event.clear()
        log.info("OrderFerry listening on %s:%d", self.bind, self.port)

        self._watchdog_thread = threading.Thread(
            target=self._watchdog,
            name="mt5-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        try:
            while self._running:
                try:
                    connection, address = listener.accept()
                    try:
                        self._configure_connection(connection)
                        ClientHandler(connection, address, self).start()
                    except Exception:
                        connection.close()
                        log.exception("could not start client handler for %s:%d", *address)
                except socket.timeout:
                    continue
                except OSError:
                    if self._running:
                        raise
                    break
        finally:
            self.stop()

    @staticmethod
    def _configure_connection(connection: socket.socket) -> None:
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except (AttributeError, OSError):
            try:
                connection.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 30_000, 5_000))
            except (AttributeError, OSError):
                pass

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

        listener, self._socket = self._socket, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.stop()
        for client in clients:
            if client is not threading.current_thread():
                client.join(timeout=2.0)

        watchdog = self._watchdog_thread
        if watchdog is not None and watchdog is not threading.current_thread():
            watchdog.join(timeout=5.0)
