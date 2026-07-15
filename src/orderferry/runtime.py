"""Thread-safe MetaTrader 5 lifecycle and connection management."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping

log = logging.getLogger("orderferry.runtime")


class ConfigurationError(ValueError):
    """Raised when the bridge environment contains invalid MT5 settings."""


@dataclass(frozen=True)
class Mt5ConnectionStatus:
    connected: bool
    error_code: int | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class Mt5Config:
    """Validated inputs used for every MT5 connection attempt."""

    path: str | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "Mt5Config":
        env = os.environ if environment is None else environment
        path = env.get("MT5_PATH", "").strip() or None
        account = env.get("MT5_ACCOUNT", "").strip()
        password = env.get("MT5_PASSWORD", "").strip() or None
        server = env.get("MT5_SERVER", "").strip() or None

        login = None
        if account:
            try:
                login = int(account)
            except ValueError as exc:
                raise ConfigurationError("MT5_ACCOUNT must be an integer account number") from exc

        return cls(path=path, login=login, password=password, server=server)

    def initialize_call(self) -> tuple[tuple[str, ...], dict[str, Any]]:
        args = (self.path,) if self.path else ()
        kwargs: dict[str, Any] = {}
        if self.login is not None:
            kwargs["login"] = self.login
        if self.password:
            kwargs["password"] = self.password
        if self.server:
            kwargs["server"] = self.server
        return args, kwargs


class Mt5Runtime:
    """Own the MT5 module, connection state, and its single call lock.

    The MetaTrader5 C extension is not thread-safe. Every callable exposed by
    this object is serialized through the same lock, including initialize and
    shutdown, so client requests and watchdog reconnects cannot overlap.
    """

    LOCK_TIMEOUT_SECONDS = 30

    def __init__(self, module: Any, config: Mt5Config):
        self._module = module
        self.config = config
        self._call_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._status = Mt5ConnectionStatus(connected=False)

    @classmethod
    def from_environment(cls) -> "Mt5Runtime":
        import MetaTrader5 as mt5

        return cls(mt5, Mt5Config.from_environment())

    @property
    def connected(self) -> bool:
        return self.status.connected

    @property
    def status(self) -> Mt5ConnectionStatus:
        with self._state_lock:
            return self._status

    def _set_status(
        self,
        connected: bool,
        error: Any = None,
    ) -> None:
        error_code = None
        error_message = None
        if isinstance(error, tuple) and len(error) >= 2:
            error_code = error[0] if isinstance(error[0], int) else None
            error_message = str(error[1])
        elif error is not None:
            error_message = str(error)
        with self._state_lock:
            self._status = Mt5ConnectionStatus(
                connected=connected,
                error_code=error_code,
                error_message=error_message,
            )

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if not self._call_lock.acquire(timeout=self.LOCK_TIMEOUT_SECONDS):
            raise TimeoutError(
                f"MT5 lock timeout ({self.LOCK_TIMEOUT_SECONDS}s) waiting for "
                f"{name}(); another call is stuck"
            )
        try:
            function = getattr(self._module, name)
            # Some MT5 extension methods reject an explicitly empty **kwargs.
            return function(*args, **kwargs) if kwargs else function(*args)
        finally:
            self._call_lock.release()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._module, name)
        if not callable(attribute):
            return attribute

        def locked_call(*args: Any, **kwargs: Any) -> Any:
            return self._call(name, *args, **kwargs)

        return locked_call

    def connect(self) -> bool:
        path_note = f" (path={self.config.path})" if self.config.path else ""
        log.info("initializing MT5%s", path_note)

        try:
            args, kwargs = self.config.initialize_call()
            connected = bool(self._call("initialize", *args, **kwargs))
        except Exception as exc:
            self._set_status(False, exc)
            log.exception("MT5 initialize raised an exception")
            return False

        if not connected:
            try:
                error = self._call("last_error")
            except Exception as exc:
                error = f"could not read last_error: {exc}"
            self._set_status(False, error)
            log.error("MT5 initialize failed: %s", error)
            return False

        self._set_status(True)
        self._log_connection_info()
        return True

    def check_connection(self) -> bool:
        try:
            terminal = self._call("terminal_info")
            connected = terminal is not None and bool(getattr(terminal, "connected", True))
        except Exception as exc:
            connected = False
            error = exc
        else:
            error = None if connected else "terminal is disconnected"
        self._set_status(connected, error)
        return connected

    def disconnect(self) -> None:
        try:
            self._call("shutdown")
        except Exception:
            log.exception("MT5 shutdown failed")
        finally:
            self._set_status(False)

    def _log_connection_info(self) -> None:
        try:
            terminal = self._call("terminal_info")
            if terminal:
                log.info(
                    "MT5 terminal: %s (build %s)",
                    getattr(terminal, "name", "?"),
                    getattr(terminal, "build", "?"),
                )
            version = self._call("version")
            if version:
                log.info("MT5 version: %s", version)
            account = self._call("account_info")
            if account:
                log.info(
                    "MT5 account: %s | balance: %s %s",
                    getattr(account, "login", "?"),
                    getattr(account, "balance", "?"),
                    getattr(account, "currency", ""),
                )
        except Exception:
            log.exception("failed to read MT5 connection details")
