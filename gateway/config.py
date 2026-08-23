"""Gateway configuration - single source of truth, env-driven."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Trust the operating-system / system CA store for outbound TLS. This makes the gateway
# work behind corporate TLS-inspecting proxies (Zscaler, etc.) whose custom root CA is in
# the OS keychain but NOT in certifi - without it, boto3 (Bedrock) and httpx calls fail
# with "self-signed certificate in certificate chain". Opt out: AGNOS_SYSTEM_TRUST=false.
if os.getenv("AGNOS_SYSTEM_TRUST", "true").lower() in ("1", "true", "yes", "on"):
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - never block startup on trust-store setup
        pass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # ── server ──
    host: str = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port: int = int(os.getenv("GATEWAY_PORT", "8090"))
    log_level: str = os.getenv("GATEWAY_LOG_LEVEL", "INFO")

    # ── engine (the swappable translation slot) ──
    # A commodity translator plugs into ONE governed slot. Any of these engines can
    # serve any provider; the governance boundary is identical for all of them.
    #   bifrost  · Go sidecar; fast default translator
    #   litellm  · LiteLLM proxy; widest provider coverage
    #   portkey  · Portkey OSS gateway; stateless (key injected per request)
    #   direct   · our own in-process adapters (owned escape hatch)
    #   echo     · deterministic $0 in-process upstream (tests)
    engine: str = os.getenv("ENGINE", "bifrost")          # bifrost | litellm | portkey | direct | echo
    bifrost_url: str = os.getenv("BIFROST_URL", "http://localhost:8099")
    # Portkey OSS gateway (stateless): boundary injects the provider key per request
    # via x-portkey-provider + Authorization / x-portkey-aws-* headers. Holds nothing.
    portkey_url: str = os.getenv("PORTKEY_URL", "http://localhost:8787")
    # LiteLLM proxy used as a STATELESS commodity translator ENGINE. It stores no
    # provider keys (no database_url / no store_model_in_db - see
    # infra/litellm-engine/config.yaml); the boundary injects the decrypted key PER
    # REQUEST via LiteLLM "clientside credentials". A compromise of this engine
    # exposes only the traffic in flight during the window - there is no key store to
    # dump (same property as the Portkey/Bifrost stateless + owned Direct engines).
    litellm_engine_url: str = os.getenv("LITELLM_ENGINE_URL", "http://localhost:4100")
    litellm_engine_key: str = os.getenv("LITELLM_ENGINE_KEY", "sk-1234")
    # Base URL of the Jaeger UI for building deep-links to a request's trace. Differs
    # by environment: dev = the direct :16686 UI; prod = "/jaeger" (served by Caddy
    # with QUERY_BASE_PATH=/jaeger). The full link is {base}/search?...
    jaeger_ui_url: str = os.getenv("JAEGER_UI_URL", "http://localhost:16686")
    governance_mode: str = os.getenv("GOVERNANCE_MODE", "full")  # full | noop
    # per-request upstream timeout (seconds) → 504; overridable via X-Gateway-Timeout
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
    # Hard upper bound for any request timeout (header, per-provider config, or the
    # gateway deadline). Long-running use cases (e.g. spec generation over millions
    # of tokens) can legitimately need 1-2 hours, so the default ceiling is 2h.
    max_request_timeout_s: int = int(os.getenv("MAX_REQUEST_TIMEOUT_S", "7200"))
    # graceful shutdown drain budget (seconds)
    shutdown_drain_seconds: float = float(os.getenv("SHUTDOWN_DRAIN_SECONDS", "5"))
    # provider-health background probe interval (seconds); 0 disables
    provider_health_interval: float = float(os.getenv("PROVIDER_HEALTH_INTERVAL", "300"))

    # ── datastore ──
    db_url: str = os.getenv(
        "GOVERNANCE_DB_URL",
        "postgresql+asyncpg://agnos:agnos@localhost:5433/agnos_gateway",
    )

    # ── encryption ──
    master_key: str = os.getenv("GATEWAY_MASTER_KEY", "")

    # ── kafka (optional) ──
    kafka_brokers: str = os.getenv("KAFKA_BROKERS", "")
    kafka_topic: str = os.getenv("KAFKA_TOPIC", "agnos-proxy.governance.v1")

    # ── redis (optional; distributed rate-limit across replicas) ──
    redis_url: str = os.getenv("REDIS_URL", "")
    instance_id: str = os.getenv("INSTANCE_ID", "gw-1")
    # budget alert webhook (Slack/generic). Empty = event-only.
    budget_webhook_url: str = os.getenv("BUDGET_WEBHOOK_URL", "")

    # ── RBAC / SSO ──
    platform_admin_token: str = os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")
    session_secret: str = os.getenv("SESSION_SECRET", "agnos-proxy-session-secret")
    # Preview mode: when on, POST /auth/preview issues a dashboard admin session
    # WITHOUT a password, so the shared link shows the live dashboard with no sign-in
    # wall (the real password stays a secret; it is never shipped to the browser).
    # Default on for the public playground build; set PREVIEW_MODE=false to require login.
    preview_mode: bool = _bool("PREVIEW_MODE", True)
    # Playground mode: this is a public, prototype demo deployment. The UI shows a "prototype"
    # ribbon and operators are expected to run it with ENGINE=echo and no real provider keys, so
    # visitors can explore the live dashboard at zero cost.
    playground_mode: bool = _bool("PLAYGROUND_MODE", False)
    dashboard_admin_user: str = os.getenv("DASHBOARD_ADMIN_USER", "admin")
    # `or "agnos"` so an EMPTY env value (e.g. a missing prod secret) falls back
    # to the dev default instead of becoming an empty password (which would let
    # anyone in). Prod sets the real password via the DASHBOARD_ADMIN_PASSWORD secret.
    dashboard_admin_password: str = os.getenv("DASHBOARD_ADMIN_PASSWORD") or "agnos"
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "")
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID", "")
    oidc_client_secret: str = os.getenv("OIDC_CLIENT_SECRET", "")
    oidc_redirect_uri: str = os.getenv("OIDC_REDIRECT_URI", "http://localhost:8090/auth/sso/callback")
    # Workspace-scoped JWT bearer auth. Claims: workspace_id, sub(user), component, roles.
    # dev-trust mode decodes-without-verify for local/demo; set false + OIDC_ISSUER for JWKS verify.
    jwt_dev_trust: bool = _bool("AGNOS_JWT_DEV_TRUST", True)
    jwt_workspace_claim: str = os.getenv("JWT_WORKSPACE_CLAIM", "workspace_id")
    jwt_component_claim: str = os.getenv("JWT_COMPONENT_CLAIM", "component")

    # ── provider creds (seed source of truth) ──
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    aws_access_key_id: str | None = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = os.getenv("AWS_SECRET_ACCESS_KEY")
    aws_session_token: str | None = os.getenv("AWS_SESSION_TOKEN")
    aws_region_name: str = os.getenv("AWS_REGION_NAME", "us-east-1")
    # Block startup on the Bifrost key reconcile (default: run it in background so
    # the gateway is ready in seconds; keys persist in Bifrost across restarts).
    bifrost_reconcile_blocking: bool = os.getenv("BIFROST_RECONCILE_BLOCKING", "false").lower() == "true"
    # Default per-request timeout (seconds) pushed to Bifrost per managed key.
    # Bifrost's own default is 30s, which 504s on long completions - raise it so
    # prod components don't fail. Admin can override per provider in its config.
    bifrost_default_timeout_s: int = int(os.getenv("BIFROST_DEFAULT_REQUEST_TIMEOUT_S", "120"))
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    azure_openai_api_key: str | None = os.getenv("AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str | None = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    # DirectEngine extra auth modes (owned-engine insourcing path).
    # Bedrock bearer API key (boto3 reads AWS_BEARER_TOKEN_BEDROCK); Vertex AI
    # service-account project + credentials JSON path. All optional - the
    # DirectEngine falls back to static AWS keys / AI-Studio api key when unset.
    aws_bedrock_api_key: str | None = os.getenv("AWS_BEDROCK_API_KEY")
    vertexai_project: str | None = os.getenv("VERTEXAI_PROJECT")
    vertexai_location: str = os.getenv("VERTEXAI_LOCATION", "us-central1")
    google_application_credentials: str | None = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    # ── demo workspace keys ──
    ws_key_primary: str = os.getenv("WS_KEY_PRIMARY", "gw-key-primary-001")
    ws_key_secondary: str = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")
    ws_key_secure: str = os.getenv("WS_KEY_SECURE", "gw-key-secure-001")
    ws_key_gemini: str = os.getenv("WS_KEY_GEMINI", "gw-key-gemini-001")


settings = Settings()
