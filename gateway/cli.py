#!/usr/bin/env python3
"""agnos - the Agnos Proxy CLI.

A dependency-free (stdlib-only) command-line wrapper around the Agnos Proxy
quickstart stack. Installed as the `agnos` console script (see [project.scripts]
in pyproject.toml) so `pipx install agnos-proxy-llm-gateway` gives you the CLI
without Docker or the gateway runtime.

Design constraints (important):
  * NO third-party dependencies - argparse/subprocess/urllib/secrets from the
    stdlib only, so the CLI installs and runs anywhere Python 3 is present.
  * NO import of the heavy gateway runtime (FastAPI app, DB, engines) at import
    time - `python -m gateway.cli` and `python gateway/cli.py` must work stand
    alone. Anything imported here must be stdlib.

It mirrors bin/agnos and shells out to `docker compose` against the prebuilt
image quickstart. Commands:

  agnos init             interactive .env wizard (engine/keys/login/port + secrets)
  agnos up               start the stack (pull the published image)
  agnos down             stop the stack
  agnos logs [service]   follow logs (all services, or one, e.g. gateway)
  agnos status           compose ps + gateway /health
  agnos update           pull the latest image and restart
  agnos config           open .env in $EDITOR
  agnos open             open the dashboard in your browser
  agnos version          print the gateway (from /health) + CLI version
  agnos help             show help
"""
from __future__ import annotations

import argparse
import os
import sys

# When launched as `python gateway/cli.py`, CPython puts the gateway/ package dir on
# sys.path[0]. That shadows any stdlib module sharing a name with a gateway subpackage
# - notably `secrets` (-> gateway/secrets/). This CLI imports nothing from its own
# directory, so drop that entry to guarantee stdlib imports resolve. It is a harmless
# no-op under `python -m gateway.cli` or the installed console script (the script dir
# is not on sys.path in those cases).
_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _SELF_DIR]

import json
import secrets
import shutil
import subprocess
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

# ── version (never import the gateway package - keep this standalone) ──────────
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        CLI_VERSION = _pkg_version("agnos-proxy-llm-gateway")
    except PackageNotFoundError:
        CLI_VERSION = "0.2.0"
except Exception:  # pragma: no cover - importlib.metadata is stdlib on 3.12
    CLI_VERSION = "0.2.0"

# ── constants ──────────────────────────────────────────────────────────────--
OWNER = "siva010928"
REPO = "agnos-proxy-oss"
IMAGE = f"ghcr.io/{OWNER}/agnos-proxy"
RAW_BASE = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main"
RAW_COMPOSE_URL = f"{RAW_BASE}/deploy/docker-compose.quickstart.yml"
INSTALL_DOCS = f"https://github.com/{OWNER}/{REPO}/blob/main/docs/INSTALL.md"
COMPOSE_FILENAME = "docker-compose.yml"
ENV_FILENAME = ".env"
DEFAULT_PORT = "8090"
ENGINES = ("echo", "direct", "bifrost", "litellm", "portkey")

# Minimal, self-contained quickstart compose (gateway + Postgres + Redis) written
# by `agnos init`/`agnos up` when no compose file is present. Kept in sync with
# deploy/docker-compose.quickstart.yml; all secrets come from the co-located .env.
QUICKSTART_COMPOSE = f"""\
# Agnos Proxy - Quickstart Stack (prebuilt image, NO build). Written by `agnos`.
# Secrets are read from the .env next to this file - never hardcoded here.
name: agnos-proxy

services:
  gateway:
    image: {IMAGE}:${{AGNOS_VERSION:-latest}}
    container_name: agnos-proxy-gateway
    env_file:
      - .env
    environment:
      GATEWAY_HOST: "0.0.0.0"
      GATEWAY_PORT: "8090"
      ENGINE: "${{ENGINE:-echo}}"
      GOVERNANCE_DB_URL: "postgresql+asyncpg://agnos:agnos@postgres:5432/agnos_gateway"
      REDIS_URL: "redis://redis:6379"
      PREVIEW_MODE: "${{PREVIEW_MODE:-false}}"
      GATEWAY_MASTER_KEY: "${{GATEWAY_MASTER_KEY:?set GATEWAY_MASTER_KEY in .env (run: agnos init)}}"
    ports:
      - "${{GATEWAY_PORT:-8090}}:8090"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8090/health"]
      interval: 10s
      timeout: 5s
      start_period: 45s
      retries: 5
    restart: unless-stopped

  postgres:
    image: postgres:16
    container_name: agnos-proxy-postgres
    environment:
      POSTGRES_USER: agnos
      POSTGRES_PASSWORD: agnos
      POSTGRES_DB: agnos_gateway
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agnos -d agnos_gateway"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

  redis:
    image: redis:7
    container_name: agnos-proxy-redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
    restart: unless-stopped

volumes:
  pg_data:
"""


# ── output helpers ────────────────────────────────────────────────────────────
def _info(msg: str) -> None:
    print(msg)


def _err(msg: str) -> None:
    print(f"agnos: {msg}", file=sys.stderr)


def _die(msg: str, code: int = 1) -> "None":
    _err(msg)
    raise SystemExit(code)


# ── docker compose resolution ──────────────────────────────────────────────────
def _resolve_dc() -> list[str]:
    """Return the docker compose invocation (v2 plugin preferred), or exit."""
    if os.environ.get("AGNOS_DC"):
        return os.environ["AGNOS_DC"].split()
    if not shutil.which("docker"):
        _die(f"docker not found. See {INSTALL_DOCS}")
    probe = subprocess.run(
        ["docker", "compose", "version"],
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    _die(f"Docker Compose not found. Install the Compose v2 plugin. See {INSTALL_DOCS}")
    return []  # unreachable; keeps type-checkers happy


def _run_compose(directory: Path, *args: str) -> int:
    """Run `docker compose -f <compose> <args...>` with cwd=directory."""
    dc = _resolve_dc()
    cmd = [*dc, "-f", COMPOSE_FILENAME, *args]
    return subprocess.run(cmd, cwd=str(directory), check=False).returncode


# ── path / config resolution ────────────────────────────────────────────────--
def _resolve_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "dir", None) or ".").expanduser().resolve()


def _ensure_compose(directory: Path, fetch: bool = False) -> Path:
    """Ensure a compose file exists in *directory*; write/fetch one if missing."""
    compose = directory / COMPOSE_FILENAME
    if compose.exists():
        return compose
    directory.mkdir(parents=True, exist_ok=True)
    if fetch:
        try:
            with urlopen(RAW_COMPOSE_URL, timeout=15) as resp:  # noqa: S310 - fixed https URL
                compose.write_text(resp.read().decode("utf-8"))
            _info(f"agnos: fetched {COMPOSE_FILENAME} from {RAW_COMPOSE_URL}")
            return compose
        except Exception as exc:  # fall back to the embedded copy
            _err(f"could not fetch compose ({exc}); writing the embedded quickstart instead")
    compose.write_text(QUICKSTART_COMPOSE)
    _info(f"agnos: wrote {COMPOSE_FILENAME} (embedded quickstart) to {directory}")
    return compose


def _read_port(directory: Path) -> str:
    """Best-effort GATEWAY_PORT from the co-located .env (default 8090)."""
    env = directory / ENV_FILENAME
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("GATEWAY_PORT="):
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    return DEFAULT_PORT


def _health(port: str) -> dict | None:
    """GET /health as parsed JSON, or None if unreachable."""
    try:
        with urlopen(f"http://localhost:{port}/health", timeout=3) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


# ── interactive prompt helpers ─────────────────────────────────────────────────
def _ask(prompt: str, default: str, no_input: bool) -> str:
    """Prompt with a default; return the default when non-interactive or on EOF."""
    if no_input or not sys.stdin.isatty():
        return default
    try:
        ans = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return ans or default


def _gen_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# ── init: the .env wizard ────────────────────────────────────────────────────--
def _render_env(engine: str, preview_mode: bool, port: str, provider_lines: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"""\
# Agnos Proxy - generated {stamp} by `agnos init`. Secrets below are random and
# unique to this install. Keep this file private; never commit it.

# ── Backend engine: echo | direct | bifrost | litellm | portkey ──
# echo   = deterministic $0 engine, needs NO provider keys (safe first run).
# direct = in-process adapters (Anthropic/Bedrock/Gemini/OpenAI-compatible).
# bifrost|litellm|portkey = optional translator sidecars (see docs/ENGINES.md).
ENGINE={engine}

# Require login (no passwordless preview). With PREVIEW_MODE=false the gateway
# refuses to boot on shipped-default secrets - which is why they are generated below.
PREVIEW_MODE={"true" if preview_mode else "false"}
GATEWAY_PORT={port}

# ── Secrets (auto-generated - do not share) ──
GATEWAY_MASTER_KEY={_gen_secret(32)}
PLATFORM_ADMIN_TOKEN={_gen_secret(32)}
SESSION_SECRET={_gen_secret(32)}

# ── Dashboard login ──
DASHBOARD_ADMIN_USER=admin
DASHBOARD_ADMIN_PASSWORD={_gen_secret(18)}

# ── Optional overrides ──
# AGNOS_VERSION=v0.2.0              # pin the image tag (default: latest)

# ── Provider keys (only needed when ENGINE=direct or a sidecar) ──
# ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
# GEMINI_API_KEY=
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_REGION_NAME=us-east-1
"""
    if provider_lines:
        body += "\n# ── Provider keys (from `agnos init`) ──\n" + "".join(
            f"{line}\n" for line in provider_lines
        )
    return body


def cmd_init(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    directory.mkdir(parents=True, exist_ok=True)
    env_path = directory / ENV_FILENAME
    no_input = bool(args.no_input)

    if env_path.exists() and not args.force:
        _die(f"{env_path} already exists. Re-run with --force to overwrite it.")

    # Engine.
    engine = (args.engine or "").strip().lower()
    if engine and engine not in ENGINES:
        _die(f"--engine must be one of {'|'.join(ENGINES)} (got '{engine}')")
    if not engine:
        _info("Engine:  1) echo (no keys, demo)   2) direct (real providers)   3) bifrost|litellm|portkey (sidecar)")
        choice = _ask("Choose [1]: ", "1", no_input)
        if choice == "2":
            engine = "direct"
        elif choice == "3":
            engine = _ask("  which sidecar (bifrost|litellm|portkey) [bifrost]: ", "bifrost", no_input).lower()
            if engine not in ENGINES:
                engine = "bifrost"
        else:
            engine = "echo"

    # Provider keys (only meaningful for non-echo engines).
    provider_lines: list[str] = []
    if engine != "echo" and not no_input and sys.stdin.isatty():
        _info("Provider keys (optional - leave blank to skip and add to .env later):")
        for key, label in (("ANTHROPIC_API_KEY", "Anthropic"), ("OPENAI_API_KEY", "OpenAI"), ("GEMINI_API_KEY", "Gemini")):
            val = _ask(f"  {label}: ", "", no_input)
            if val:
                provider_lines.append(f"{key}={val}")

    # Login / preview mode.
    if args.preview:
        preview_mode = True
    elif no_input:
        preview_mode = False
    else:
        _info("Require login?  1) yes - generate a strong admin password [default]   2) no - open dashboard (preview)")
        preview_mode = _ask("Choose [1]: ", "1", no_input) == "2"

    # Port.
    port = (args.port or _ask(f"Host port [{DEFAULT_PORT}]: ", DEFAULT_PORT, no_input)).strip() or DEFAULT_PORT

    env_path.write_text(_render_env(engine, preview_mode, port, provider_lines))
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    _info(f"agnos: wrote {env_path} (engine={engine}, login={'required' if not preview_mode else 'open-preview'}, port={port})")

    _ensure_compose(directory, fetch=bool(args.fetch_compose))

    _info("")
    _info("Next: start the stack with")
    _info(f"    agnos up{'' if directory == Path('.').resolve() else f' --dir {directory}'}")
    return 0


# ── stack lifecycle commands ─────────────────────────────────────────────────--
def cmd_up(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    if not (directory / ENV_FILENAME).exists():
        _die(f"no {ENV_FILENAME} in {directory}. Run `agnos init` first (see {INSTALL_DOCS}).")
    _ensure_compose(directory)
    rc = _run_compose(directory, "up", "-d", "--pull", "always")
    if rc == 0:
        port = _read_port(directory)
        _info(f"agnos: stack started. Dashboard: http://localhost:{port}/")
    return rc


def cmd_down(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    return _run_compose(directory, "down")


def cmd_logs(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    extra = ["logs", "-f"]
    if args.service:
        extra.append(args.service)
    return _run_compose(directory, *extra)


def cmd_status(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    rc = _run_compose(directory, "ps")
    port = _read_port(directory)
    _info(f"\nHealth (http://localhost:{port}/health):")
    health = _health(port)
    if health is not None:
        _info(f"  ok - version={health.get('version', '?')} engine/playground={health.get('playground', '?')}")
    else:
        _info("  unreachable - is the stack up? (agnos up)")
    return rc


def cmd_update(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    _ensure_compose(directory)
    rc = _run_compose(directory, "pull")
    if rc == 0:
        rc = _run_compose(directory, "up", "-d")
    if rc == 0:
        _info("agnos: updated to the latest image.")
    return rc


def cmd_config(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    env_path = directory / ENV_FILENAME
    if not env_path.exists():
        _die(f"no {ENV_FILENAME} in {directory}. Run `agnos init` first.")
    editor = os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")
    return subprocess.run([editor, str(env_path)], check=False).returncode


def cmd_open(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    port = _read_port(directory)
    url = f"http://localhost:{port}/"
    if not webbrowser.open(url):
        _info(f"Open this in your browser: {url}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    directory = _resolve_dir(args)
    port = _read_port(directory)
    health = _health(port)
    if health is not None and health.get("version"):
        _info(f"gateway: {health['version']}")
    else:
        _info("gateway: unreachable (start it with: agnos up)")
    _info(f"agnos CLI: {CLI_VERSION}")
    return 0


# ── argument parser ────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agnos",
        description="Manage the Agnos Proxy quickstart stack (a thin wrapper over docker compose).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Run from your install dir (docker-compose.yml + .env) or use --dir.\nDocs: {INSTALL_DOCS}",
    )
    parser.add_argument("--version", action="version", version=f"agnos CLI {CLI_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def _with_dir(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--dir", metavar="DIR", help="directory holding docker-compose.yml + .env (default: .)")
        return p

    p_init = _with_dir(sub.add_parser("init", help="interactive .env wizard (secrets, engine, login, port)"))
    p_init.add_argument("--force", action="store_true", help="overwrite an existing .env")
    p_init.add_argument("--engine", choices=ENGINES, help="engine (skips the prompt)")
    p_init.add_argument("--port", help="host port (skips the prompt)")
    p_init.add_argument("--preview", action="store_true", help="open dashboard without login (PREVIEW_MODE=true)")
    p_init.add_argument("--no-input", action="store_true", help="non-interactive: use defaults/flags, no prompts")
    p_init.add_argument("--fetch-compose", action="store_true", help="download the compose file from GitHub instead of embedding")
    p_init.set_defaults(func=cmd_init)

    _with_dir(sub.add_parser("up", help="start the stack (docker compose up -d --pull always)")).set_defaults(func=cmd_up)
    _with_dir(sub.add_parser("down", help="stop the stack (docker compose down)")).set_defaults(func=cmd_down)

    p_logs = _with_dir(sub.add_parser("logs", help="follow logs (all services, or one)"))
    p_logs.add_argument("service", nargs="?", help="optional single service (e.g. gateway)")
    p_logs.set_defaults(func=cmd_logs)

    _with_dir(sub.add_parser("status", help="container status + gateway /health")).set_defaults(func=cmd_status)
    _with_dir(sub.add_parser("update", help="pull the latest image and restart")).set_defaults(func=cmd_update)
    _with_dir(sub.add_parser("config", help="open .env in $EDITOR")).set_defaults(func=cmd_config)
    _with_dir(sub.add_parser("open", help="open the dashboard in your browser")).set_defaults(func=cmd_open)
    _with_dir(sub.add_parser("version", help="print gateway (from /health) + CLI version")).set_defaults(func=cmd_version)
    sub.add_parser("help", help="show this help")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command or args.command == "help":
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
