# Security policy

## Supported versions

Security fixes are provided for the latest released version of OrderFerry.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub Security Advisories:

1. Open the repository's **Security** tab.
2. Select **Advisories** and **Report a vulnerability**.
3. Include affected versions, reproduction steps, impact, and any suggested fix.

Do not disclose an unpatched vulnerability in a public issue. We will
acknowledge a report as soon as practical and coordinate disclosure after a fix
is available.

## Deployment boundary

OrderFerry is designed to bind to loopback. It does not implement
authentication, authorization, or transport encryption. Anyone who can connect
to the relay can invoke its allowlisted operations, including trade execution
and account switching.

Never expose OrderFerry directly to the public internet. For remote operation:

- use an authenticated VPN or tunnel;
- scope the Windows Firewall rule to exact trusted addresses;
- keep the host and MetaTrader terminal patched;
- use a dedicated least-privilege Windows account; and
- start with a demo trading account.

Never include MT5 passwords in bug reports, logs, screenshots, or repository
files. Prefer the terminal's existing authenticated session; if environment
variables are required, protect them as secrets at the process boundary.
