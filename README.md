# OrderFerry

[![CI](https://github.com/ElyzeSolutions/OrderFerry/actions/workflows/ci.yml/badge.svg)](https://github.com/ElyzeSolutions/OrderFerry/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

OrderFerry is a small, hardened JSON-over-TCP relay for MetaTrader 5. It gives
local automation a stable interface for market data, account state, tick
subscriptions, and trade execution while keeping every call into the MT5 Python
extension serialized on one thread-safe boundary.

> [!IMPORTANT]
> OrderFerry has no built-in authentication or TLS. It binds to
> `127.0.0.1` by default. Never expose it directly to the public internet.
> For remote use, restrict the firewall to known peers and use a private VPN or
> authenticated tunnel.

OrderFerry is an independent project and is not affiliated with, endorsed by,
or sponsored by MetaQuotes Ltd. Trading involves risk; test on a demo account
before using any automation with real funds.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- A running MetaTrader 5 terminal, logged into a valid account
- **Algo Trading** enabled in that terminal when sending trades
- [`uv`](https://docs.astral.sh/uv/) for reproducible installation

## Quick start

```powershell
git clone https://github.com/ElyzeSolutions/OrderFerry.git
Set-Location OrderFerry
uv sync --locked
uv run orderferry --test
uv run orderferry
```

The relay starts in degraded mode when MT5 is unavailable and reconnects every
30 seconds. This prevents a temporary terminal or broker outage from crashing
the scheduled task.

If automatic terminal discovery chooses the wrong installation, provide its
path explicitly:

```powershell
$env:MT5_PATH = "C:\Program Files\MetaTrader 5\terminal64.exe"
uv run orderferry --test
```

## Protocol

Each request and response is one UTF-8 JSON object followed by `\n`. Requests
have a `method`, optional positional `args`, optional `kwargs`, and an optional
`id` copied to the response.

```python
import json
import socket

with socket.create_connection(("127.0.0.1", 18812), timeout=5) as connection:
    request = {"id": 1, "method": "symbol_info_tick", "args": ["XAUUSD"]}
    connection.sendall((json.dumps(request) + "\n").encode())
    response = connection.makefile("r", encoding="utf-8").readline()
    print(json.loads(response))
```

Successful responses use `{"ok": true, "result": ...}`. Failures use a
stable error envelope:

```json
{
  "ok": false,
  "error": {
    "code": "MT5_DISCONNECTED",
    "message": "MetaTrader is disconnected; the watchdog is reconnecting",
    "method": "account_info"
  }
}
```

### Built-in methods

| Area | Methods |
| --- | --- |
| Health | `__ping__`, `__status__`, `__constants__` |
| Streaming | `__subscribe__`, `__unsubscribe__` |
| Market data | `symbol_info`, `symbol_info_tick`, `symbols_get`, `copy_rates_*`, `copy_ticks_*` |
| Account | `account_info`, `positions_*`, `orders_*`, `history_orders_*`, `history_deals_*` |
| High-level trading | `buy`, `sell`, `buy_limit`, `sell_limit`, `buy_stop`, `sell_stop` |
| Position/order control | `close_position`, `close_all`, `modify_position`, `modify_order`, `cancel_order` |
| Raw MT5 | `order_check`, `order_send`, `login`, `last_error`, `terminal_info`, `version` |

Subscribe with symbols in `args` and an optional interval in milliseconds:

```json
{"id": 2, "method": "__subscribe__", "args": ["XAUUSD", "EURUSD"], "kwargs": {"interval_ms": 200}}
```

The server then emits `{"event":"tick", ...}` objects when bid or ask changes.
The minimum interval is 50 ms.

## Configuration

| Variable | Purpose |
| --- | --- |
| `ORDERFERRY_BIND` | Listen address; defaults to `127.0.0.1` |
| `ORDERFERRY_PORT` | Listen port; defaults to `18812` |
| `ORDERFERRY_LOG_DIR` | Rotating log directory; defaults to `./logs` |
| `MT5_PATH` | Optional path to `terminal64.exe` |
| `MT5_ACCOUNT` | Optional numeric account login |
| `MT5_PASSWORD` | Optional account password |
| `MT5_SERVER` | Optional exact broker server name |
| `MT5_SYMBOL` | Symbol used by `--test`; defaults to `XAUUSD` |

When MT5 is already logged in, leave the four `MT5_*` connection variables
unset. Credentials are never required in source files or command arguments.

## Scheduled task installation

Open an elevated PowerShell prompt from the repository:

```powershell
.\setup-host.ps1
```

The installer synchronizes the locked production environment, registers an
interactive-user scheduled task, starts it, and verifies `__ping__`. Interactive
logon is required because the MT5 terminal is a desktop application.

Remote access must be explicit and firewall-scoped:

```powershell
.\setup-host.ps1 -BindAddress 0.0.0.0 -RemoteAddress 10.20.30.40
```

Multiple trusted networks can be supplied as an array. For example, to allow a
specific LAN and the Tailscale CGNAT range:

```powershell
.\setup-host.ps1 `
  -BindAddress 0.0.0.0 `
  -RemoteAddress @("192.168.88.0/24", "100.64.0.0/10")
```

Remove the task and its firewall rule with:

```powershell
.\setup-host.ps1 -Uninstall
```

## Development

```powershell
uv sync --locked
uv run ruff format --check src tests
uv run ruff check src tests
uv run python -m unittest discover -s tests -v
uv build
```

The source is split by responsibility: `runtime` owns the MT5 lifecycle and
lock, `protocol` owns the allowlist, `trading` builds trade requests,
`serialization` owns JSON conversion, and `server` owns TCP client lifecycles.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[Apache License 2.0](LICENSE).
