"""Unified sanity CLI for the Agnos Proxy.

Runs REAL calls through the live gateway (OpenAI SDK on the real wire) for every
provider whose credentials are present, and verifies governance/usage, exception
mapping, extra-param passthrough, Bedrock auth modes, engine parity and pricing.

    python -m scripts.sanity all                 # everything, one summary
    python -m scripts.sanity calls               # chat + embeddings
    python -m scripts.sanity governance          # usage/cost recorded per call
    python -m scripts.sanity exceptions          # openai.* native errors
    python -m scripts.sanity passthrough         # provider-native extra params
    python -m scripts.sanity authmodes           # bedrock static/bearer/sso
    python -m scripts.sanity parity              # bifrost vs direct, scored
    python -m scripts.sanity pricing             # synced pricing + cost applied
    python -m scripts.sanity observability       # metrics + governance row + Jaeger trace
    python -m scripts.sanity filters             # hierarchical cascading filter facets
    python -m scripts.sanity catalog --full      # reachability across Agnos Proxy catalog

Options: --engine bifrost|direct|both (default both) · --env-file PATH ·
         --target KEY (repeatable) · --max-models N · --full
Requires the gateway running (default http://localhost:8090; SANITY_GATEWAY_URL to override).
"""
from __future__ import annotations

import sys

from scripts.sanity._client import Admin
from scripts.sanity._env import available, load_env
from scripts.sanity._reporting import c, summarize
from scripts.sanity import commands as cmds

_COMMANDS = ("provision", "calls", "governance", "exceptions", "passthrough",
             "authmodes", "parity", "pricing", "observability", "filters", "routing",
             "providertest", "availability", "consumers", "catalog")


def _parse(argv: list[str]) -> dict:
    opts = {"command": "all", "engine": "both", "env_file": None,
            "targets": [], "max_models": 3, "full": False}
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _COMMANDS or a == "all":
            opts["command"] = a
        elif a == "--engine":
            i += 1; opts["engine"] = argv[i]
        elif a == "--env-file":
            i += 1; opts["env_file"] = argv[i]
        elif a == "--target":
            i += 1; opts["targets"].append(argv[i])
        elif a == "--max-models":
            i += 1; opts["max_models"] = int(argv[i])
        elif a == "--full":
            opts["full"] = True
        elif a in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        else:
            rest.append(a)
        i += 1
    return opts


def main(argv: list[str]) -> int:
    opts = _parse(argv)
    engines = ("bifrost", "direct") if opts["engine"] == "both" else (opts["engine"],)

    admin = Admin()
    if not admin.health():
        print(c(f"\n✗ Gateway not reachable at {admin.base} - start it first "
                f"(python gateway_server.py) or set SANITY_GATEWAY_URL.", "FAIL"))
        return 2

    env = load_env(opts["env_file"])
    specs = available(env)
    if opts["targets"]:
        specs = [s for s in specs if s.key in opts["targets"] or s.provider in opts["targets"]]
    if not specs:
        print(c("\n✗ No providers with credentials found in the env. "
                "Fill providers.env-template or pass --env-file.", "FAIL"))
        return 2

    print(c(f"\nGateway: {admin.base}   engines: {','.join(engines)}   "
            f"providers: {', '.join(s.key for s in specs)}", "B"))

    cmd = opts["command"]
    sections = []
    order = _COMMANDS if cmd == "all" else (cmd,)
    for name in order:
        if name == "provision":
            sections += cmds.cmd_provision(admin, specs, engines)
        elif name == "calls":
            sections += cmds.cmd_calls(admin, specs, engines)
        elif name == "governance":
            sections += cmds.cmd_governance(admin, specs, engines)
        elif name == "exceptions":
            sections += cmds.cmd_exceptions(admin, specs, engines)
        elif name == "passthrough":
            sections += cmds.cmd_passthrough(admin, specs, engines)
        elif name == "authmodes":
            sections += cmds.cmd_authmodes(admin, specs, engines)
        elif name == "parity":
            sections += cmds.cmd_parity(admin, specs, engines)
        elif name == "pricing":
            sections += cmds.cmd_pricing(admin, specs, engines)
        elif name == "observability":
            sections += cmds.cmd_observability(admin, specs, engines)
        elif name == "filters":
            sections += cmds.cmd_filters(admin, specs, engines)
        elif name == "routing":
            sections += cmds.cmd_routing(admin, specs, engines)
        elif name == "providertest":
            sections += cmds.cmd_providertest(admin, specs, engines)
        elif name == "availability":
            sections += cmds.cmd_availability(admin, specs, engines)
        elif name == "consumers":
            sections += cmds.cmd_consumers(admin, specs, engines)
        elif name == "catalog":
            sections += cmds.cmd_catalog(admin, specs, engines,
                                         max_models=opts["max_models"], full=opts["full"])
    return summarize(cmd, sections)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
