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

The `OrderFerry` task:

- runs from the repository's locked `.venv` using `python -m orderferry`;
- starts when the installing Windows user logs on;
- runs with limited privileges after installation;
- restarts up to ten times at one-minute intervals after an unexpected exit;
- writes rotating logs to `./logs/orderferry.log`; and
- ignores duplicate starts while an instance is already running.

Rerunning `setup-host.ps1` updates the existing task and firewall rule, then
restarts and verifies the relay.

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

The firewall rule permits inbound TCP only on Domain and Private profiles.
Public networks remain blocked. `100.64.0.0/10` covers Tailscale's CGNAT address
space; exact Tailscale peer addresses are safer when only a few clients need
access. Tailnet policy should also restrict which peers can reach the host.

Check the active Windows network category before troubleshooting remote access:

```powershell
Get-NetConnectionProfile |
  Select-Object InterfaceAlias, NetworkCategory, IPv4Connectivity
```

Do not mark an untrusted public network as Private merely to make the firewall
rule apply.

### Verify the installation

Check the scheduled task and its most recent result:

```powershell
Get-ScheduledTask -TaskName OrderFerry |
  Select-Object TaskName, State

Get-ScheduledTaskInfo -TaskName OrderFerry |
  Select-Object LastRunTime, LastTaskResult
```

Confirm that OrderFerry owns the expected listener and inspect its firewall
scope:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 18812 |
  Select-Object LocalAddress, LocalPort, OwningProcess

Get-NetFirewallRule -DisplayName OrderFerry |
  Get-NetFirewallAddressFilter |
  Select-Object RemoteAddress
```

Send a real protocol status request from PowerShell:

```powershell
$client = [Net.Sockets.TcpClient]::new("127.0.0.1", 18812)
try {
  $stream = $client.GetStream()
  $request = [Text.Encoding]::UTF8.GetBytes(
    "{`"id`":`"verify`",`"method`":`"__status__`"}`n"
  )
  $stream.Write($request, 0, $request.Length)
  $reader = [IO.StreamReader]::new($stream)
  $reader.ReadLine() | ConvertFrom-Json
} finally {
  $client.Dispose()
}
```

For a remote client, replace `127.0.0.1` with the OrderFerry host's LAN or
Tailscale address. A remote TCP-only check is also available:

```powershell
Test-NetConnection <ORDERFERRY_HOST> -Port 18812
```

Follow the live service log with:

```powershell
Get-Content .\logs\orderferry.log -Tail 50 -Wait
```

### Troubleshooting

- **Port 18812 is already in use:** the installer stops before changing the
  task. Find the owner with `Get-NetTCPConnection -State Listen -LocalPort
  18812`, stop the conflicting process, or install with a different `-Port`.
- **`LastTaskResult` is `267009` (`0x41301`):** this means the task is currently
  running; it is not an error. If the task has stopped with another result,
  inspect `./logs/orderferry.log` and the Task Scheduler history.
- **Local access works but remote access fails:** confirm that the server was
  installed with a non-loopback `-BindAddress`, the client address falls within
  `-RemoteAddress`, and the active interface uses the Domain or Private profile.
- **MT5 reports disconnected:** keep the Windows user logged on, open the MT5
  terminal, confirm the trading account is connected, and enable Algo Trading.
  OrderFerry remains available in degraded mode and retries automatically.
- **The task exits immediately:** check whether another process owns the port,
  then review the log for configuration, bind, or Python environment errors.

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
