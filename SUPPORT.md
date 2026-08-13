# Getting help

Thanks for using Agnos Proxy. Here is the fastest path to an answer:

- **Docs first** - the [README](README.md), [docs/ENGINES.md](docs/ENGINES.md), and the in-app **Docs** page (`/app/docs`) cover setup, engines, routing, and the API.
- **Questions / ideas / "how do I...?"** - open a [GitHub Discussion](https://github.com/siva010928/agnos-proxy-oss/discussions).
- **Bugs** - open a [Bug report](https://github.com/siva010928/agnos-proxy-oss/issues/new/choose) with repro steps, expected vs actual, and logs.
- **Feature requests** - open a [Feature request](https://github.com/siva010928/agnos-proxy-oss/issues/new/choose); describe the problem before the solution.
- **Security vulnerabilities** - do **not** open a public issue; follow [SECURITY.md](SECURITY.md).

## Before you file

- Confirm you are on the latest `main`.
- Include: your OS, Python/Node versions, the active `ENGINE`, and relevant gateway logs (`.gateway.log` or `docker compose logs`).
- Redact secrets. Never paste real provider keys.

## Common issues

- **Provider "connection error / self-signed certificate in certificate chain"** - you are likely behind a corporate TLS-inspecting proxy. Agnos Proxy trusts the OS certificate store by default (`AGNOS_SYSTEM_TRUST=true`); make sure your proxy's root CA is installed in the OS/container trust store.
- **404 "model alias not registered"** - use `default` or an alias registered for the workspace (see the Routing page).
- **401 / 402 / 422 / 429** - see the error table in the README (auth, budget, guardrail, rate-limit).
