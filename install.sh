#!/bin/sh
# Agnos Proxy - one-command secure self-host install.
#
#   curl -fsSL https://raw.githubusercontent.com/siva010928/agnos-proxy-oss/main/install.sh | sh
#
# What it does (idempotent - safe to re-run):
#   1. checks Docker + the Docker Compose v2 plugin are present,
#   2. picks a workdir and drops a `docker-compose.yml` (the prebuilt-image quickstart),
#   3. generates a `.env` with STRONG random secrets if one does not exist yet,
#   4. pulls the published image and brings the stack up,
#   5. waits for /health, then prints the dashboard URL + the generated admin login.
#
# It NEVER overwrites an existing .env, and it prints the admin password only when it
# generated the .env in this run. Full docs: docs/INSTALL.md.
set -eu

# ── constants ────────────────────────────────────────────────────────────────
RAW_BASE="https://raw.githubusercontent.com/siva010928/agnos-proxy-oss/main"
COMPOSE_URL="$RAW_BASE/deploy/docker-compose.quickstart.yml"
INSTALL_DOCS="https://github.com/siva010928/agnos-proxy-oss/blob/main/docs/INSTALL.md"
IMAGE="ghcr.io/siva010928/agnos-proxy"

# ── tiny output helpers (plain text, no color dependency) ─────────────────────
step() { printf '\n==> %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
die()  { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

# ── 1. prerequisites ──────────────────────────────────────────────────────────
step "Checking prerequisites (docker, docker compose, curl, openssl)"
command -v docker  >/dev/null 2>&1 || die "docker not found. Install Docker: https://docs.docker.com/get-docker/ (see $INSTALL_DOCS)"
command -v curl    >/dev/null 2>&1 || die "curl not found. Install curl and re-run (see $INSTALL_DOCS)"
command -v openssl >/dev/null 2>&1 || die "openssl not found. Install openssl and re-run (see $INSTALL_DOCS)"

# Prefer the Compose v2 plugin (`docker compose`); fall back to legacy `docker-compose`.
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "Docker Compose not found. Install the Compose v2 plugin: https://docs.docker.com/compose/install/ (see $INSTALL_DOCS)"
fi
info "docker + '$DC' found"

# ── 2. choose a workdir ─────────────────────────────────────────────────────--
# Reuse the current directory only if it already holds OUR quickstart compose
# (i.e. a previous install here). Otherwise install into ./agnos-proxy so we never
# clobber an unrelated docker-compose.yml (e.g. a source checkout's dev stack).
if [ -f docker-compose.yml ] && grep -q "$IMAGE" docker-compose.yml 2>/dev/null; then
  WORKDIR="$(pwd)"
else
  WORKDIR="$(pwd)/agnos-proxy"
  mkdir -p "$WORKDIR"
fi
cd "$WORKDIR"
step "Workdir: $WORKDIR"

# ── 3. obtain the quickstart compose file ─────────────────────────────────────
# Prefer a copy that ships next to this script (works offline / from a clone);
# otherwise download it from GitHub. Written as docker-compose.yml in the workdir.
SRC=""
if [ -n "${0:-}" ] && [ -f "$0" ]; then
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  for cand in "$SCRIPT_DIR/deploy/docker-compose.quickstart.yml" "$SCRIPT_DIR/docker-compose.quickstart.yml"; do
    [ -f "$cand" ] && { SRC="$cand"; break; }
  done
fi
step "Fetching the quickstart compose file"
if [ -n "$SRC" ]; then
  cp "$SRC" docker-compose.yml
  info "copied from $SRC"
else
  curl -fsSL "$COMPOSE_URL" -o docker-compose.yml || die "failed to download $COMPOSE_URL"
  info "downloaded from $COMPOSE_URL"
fi

# Drop the `agnos` CLI wrapper next to the compose file for convenience (optional -
# the stack works without it). Prefer a co-located copy, else download it.
AGNOS_SRC=""
if [ -n "${SCRIPT_DIR:-}" ]; then
  for cand in "$SCRIPT_DIR/bin/agnos" "$SCRIPT_DIR/agnos"; do
    [ -f "$cand" ] && { AGNOS_SRC="$cand"; break; }
  done
fi
if [ -n "$AGNOS_SRC" ]; then
  cp "$AGNOS_SRC" agnos && chmod +x agnos && info "installed ./agnos CLI (copied)"
elif curl -fsSL "$RAW_BASE/bin/agnos" -o agnos 2>/dev/null; then
  chmod +x agnos && info "installed ./agnos CLI"
else
  info "skipped ./agnos CLI (optional; add it later - see docs/INSTALL.md)"
fi

# ── interactive setup wizard (only on a real terminal; piped `curl|sh` keeps secure defaults) ──
# Offer choices instead of defaulting everything. Skipped when stdin is not a TTY
# (piped install), when AGNOS_NONINTERACTIVE=1, or when a .env already exists.
ENGINE_CHOICE="echo"; PREVIEW_CHOICE="false"; PORT_CHOICE="8090"; PROVIDER_ENV=""
if [ -t 0 ] && [ "${AGNOS_NONINTERACTIVE:-0}" != "1" ] && [ ! -f .env ]; then
  step "Interactive setup (press Enter to accept the [default])"
  printf '    Engine   1) echo - no keys, demo/look-around   2) direct - real providers, in-process   3) sidecar (bifrost|litellm|portkey)\n'
  printf '    Choose [1]: '; read -r _e || _e=""
  case "$_e" in
    2) ENGINE_CHOICE="direct" ;;
    3) printf '      which sidecar (bifrost|litellm|portkey) [bifrost]: '; read -r _s || _s=""; ENGINE_CHOICE="${_s:-bifrost}" ;;
    *) ENGINE_CHOICE="echo" ;;
  esac
  if [ "$ENGINE_CHOICE" != "echo" ]; then
    info "Provider keys (optional - leave blank to skip and add to .env later)"
    for _pair in "ANTHROPIC_API_KEY:Anthropic" "OPENAI_API_KEY:OpenAI" "GEMINI_API_KEY:Gemini"; do
      _k="${_pair%%:*}"; _label="${_pair##*:}"
      printf '      %s: ' "$_label"; read -r _v || _v=""
      [ -n "$_v" ] && PROVIDER_ENV="${PROVIDER_ENV}${_k}=${_v}
"
    done
    printf '      AWS_ACCESS_KEY_ID (blank to skip): '; read -r _ak || _ak=""
    if [ -n "$_ak" ]; then
      printf '      AWS_SECRET_ACCESS_KEY: '; read -r _sk || _sk=""
      PROVIDER_ENV="${PROVIDER_ENV}AWS_ACCESS_KEY_ID=${_ak}
AWS_SECRET_ACCESS_KEY=${_sk}
AWS_REGION_NAME=us-east-1
"
    fi
  fi
  printf '    Require login?   1) yes - generate a strong admin password [default]   2) no - open dashboard (demo/preview)\n'
  printf '    Choose [1]: '; read -r _m || _m=""
  case "$_m" in 2) PREVIEW_CHOICE="true" ;; *) PREVIEW_CHOICE="false" ;; esac
  printf '    Host port [8090]: '; read -r _p || _p=""; PORT_CHOICE="${_p:-8090}"
  printf '\n    -> engine=%s, login=%s, port=%s\n' "$ENGINE_CHOICE" "$([ "$PREVIEW_CHOICE" = false ] && echo required || echo open-preview)" "$PORT_CHOICE"
  printf '    Proceed? [Y/n]: '; read -r _c || _c=""; case "$_c" in [Nn]*) die "aborted by user" ;; esac
fi

# ── 4. generate .env with strong secrets (never overwrite an existing one) ─────
# urlsafe base64, padding stripped - accepted anywhere a token/passphrase is used.
rand_urlsafe() { openssl rand -base64 "${1:-32}" | tr '+/' '-_' | tr -d '='; }

GENERATED_ENV=0
if [ -f .env ]; then
  step "Using existing .env (left untouched)"
else
  step "Generating .env with strong random secrets"
  MASTER_KEY="$(rand_urlsafe 32)"       # GATEWAY_MASTER_KEY - Fernet vault key
  ADMIN_TOKEN="$(rand_urlsafe 32)"      # PLATFORM_ADMIN_TOKEN
  SESSION_SECRET="$(rand_urlsafe 32)"   # SESSION_SECRET - session signing
  ADMIN_PASS="$(rand_urlsafe 18)"       # DASHBOARD_ADMIN_PASSWORD (~24 chars)
  umask 077   # .env holds secrets - make it owner-only from the start
  cat > .env <<EOF
# Agnos Proxy - generated $(date -u '+%Y-%m-%dT%H:%M:%SZ'). Secrets below are random
# and unique to this install. Keep this file private; never commit it.

# ── Backend engine ──
# echo  = deterministic \$0 engine, needs NO provider keys (safe first run / look-around).
# direct= in-process adapters (Anthropic/Bedrock/Gemini/OpenAI-compatible), no sidecar.
# To use real providers: set ENGINE=direct and add the matching provider key(s) below.
# Other engines (bifrost|litellm|portkey) are optional sidecars - see docs/ENGINES.md.
ENGINE=$ENGINE_CHOICE

# Require login (no passwordless preview). The gateway refuses to boot on default
# secrets while this is false - which is why the strong secrets below are generated.
PREVIEW_MODE=$PREVIEW_CHOICE
GATEWAY_PORT=$PORT_CHOICE

# ── Secrets (auto-generated - do not share) ──
GATEWAY_MASTER_KEY=$MASTER_KEY
PLATFORM_ADMIN_TOKEN=$ADMIN_TOKEN
SESSION_SECRET=$SESSION_SECRET

# ── Dashboard login ──
DASHBOARD_ADMIN_USER=admin
DASHBOARD_ADMIN_PASSWORD=$ADMIN_PASS

# ── Optional overrides ──
# GATEWAY_PORT=8090                 # host port for the dashboard/API
# AGNOS_VERSION=v0.2.0              # pin the image tag (default: latest)

# ── Provider keys (only needed when ENGINE=direct or a sidecar) ──
# ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
# GEMINI_API_KEY=
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_REGION_NAME=us-east-1
EOF
  [ -n "$PROVIDER_ENV" ] && printf '%s' "$PROVIDER_ENV" >> .env
  GENERATED_ENV=1
  info ".env created (chmod 600)"
fi

# Resolve the host port for the health check + printed URLs (default 8090).
PORT="$(grep -E '^GATEWAY_PORT=' .env 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
[ -n "${PORT:-}" ] || PORT=8090

# ── 5. bring the stack up (pull the published image) ──────────────────────────
step "Starting Agnos Proxy ($DC up -d --pull always)"
if ! $DC up -d --pull always; then
  printf '\n' >&2
  die "compose up failed. If the image is private you must 'docker login ghcr.io' first; the published image must be public to pull anonymously. See $INSTALL_DOCS"
fi

# ── 6. wait for /health, then report ─────────────────────────────────────────
step "Waiting for the gateway to answer /health (up to 60s)"
HEALTH_URL="http://localhost:$PORT/health"
UP=0
i=0
while [ "$i" -lt 60 ]; do
  if curl -fsS -o /dev/null --max-time 3 "$HEALTH_URL" 2>/dev/null; then UP=1; break; fi
  i=$((i + 1))
  sleep 1
done

DASH_URL="http://localhost:$PORT/"
if [ "$UP" = "1" ]; then
  printf '\n'
  printf '================================================================\n'
  printf '  Agnos Proxy is up.\n\n'
  printf '  Dashboard:  %s\n' "$DASH_URL"
  printf '  Health:     %s\n' "$HEALTH_URL"
  if [ "$GENERATED_ENV" = "1" ]; then
    printf '\n  Admin login (generated - save these now):\n'
    printf '    user:      admin\n'
    printf '    password:  %s\n' "$ADMIN_PASS"
  else
    printf '\n  Admin login: see DASHBOARD_ADMIN_USER / DASHBOARD_ADMIN_PASSWORD in %s/.env\n' "$WORKDIR"
  fi
  printf '\n  Next steps:\n'
  printf '    - Use real providers: set ENGINE=direct in .env and add a provider key\n'
  printf '      (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / AWS_*), then:\n'
  printf '        %s up -d\n' "$DC"
  printf '    - Add an optional translator sidecar (bifrost|litellm|portkey): docs/ENGINES.md\n'
  printf '    - Manage the stack from here with:  ./agnos status | logs | update | down\n'
  printf '================================================================\n'
else
  printf '\n' >&2
  printf 'The stack started but /health did not answer within 60s.\n' >&2
  printf 'Check the logs:  (cd %s && %s logs gateway)\n' "$WORKDIR" "$DC" >&2
  printf 'Docs: %s\n' "$INSTALL_DOCS" >&2
  exit 1
fi
