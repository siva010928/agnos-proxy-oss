"""Metadata for each engine that can occupy the swappable translation slot(s).

Single source of truth behind the "Engine Slot" screen and the Quarantine &
Evacuate control. Since the Feb-2026 stateless migration, EVERY engine we run
holds NO provider keys: the key is decrypted from OUR vault and injected per
request (Bifrost direct-key, LiteLLM clientside credentials, Portkey
headers) or used in-process (Direct). So a compromise of any engine has no key
store to dump - the whole point.

We also pick engines by CAPABILITY, not just security, and can run MORE THAN ONE
at a time via per-provider routing (runtime.select_engine keys off provider):
  - litellm : unmatched provider coverage (100+, incl. watsonx / databricks /
              snowflake / sagemaker that Bifrost does not support)
  - bifrost : blazing-fast Go translator (~11 microsecond overhead)
  - portkey : clean stateless default
  - direct  : owned, in-process, zero third-party

The STATEFUL danger (a translator that stores every key) is what you get from
running "LiteLLM as your WHOLE gateway", NOT how we run our engine slot.
"""
from __future__ import annotations

# order = the routing/insourcing spectrum
ENGINE_META: dict[str, dict] = {
    "litellm": {
        "label": "LiteLLM", "vendor": "BerriAI", "runtime": "Python", "license": "MIT",
        "stateful": False, "holds_provider_keys": False, "owned": False,
        "blast_radius": "low",
        "capability": "widest provider coverage (100+ incl. watsonx/databricks/snowflake)",
        "tagline": "STATELESS translator (clientside creds) · widest provider coverage (100+)",
    },
    "bifrost": {
        "label": "Bifrost", "vendor": "Maxim", "runtime": "Go", "license": "Apache-2.0",
        "stateful": False, "holds_provider_keys": False, "owned": False,
        "blast_radius": "low",
        "capability": "blazing-fast Go translator (~11us overhead)",
        "tagline": "STATELESS translator (direct-key) · blazing-fast (Go)",
    },
    "portkey": {
        "label": "Portkey", "vendor": "Portkey", "runtime": "TypeScript/Node", "license": "MIT",
        "stateful": False, "holds_provider_keys": False, "owned": False,
        "blast_radius": "low",
        "capability": "clean stateless default",
        "tagline": "STATELESS translator · holds no keys · boundary injects per request",
    },
    "direct": {
        "label": "DirectEngine", "vendor": "Agnos Proxy (owned)", "runtime": "Python (in-process)", "license": "owned",
        "stateful": False, "holds_provider_keys": False, "owned": True,
        "blast_radius": "minimal",
        "capability": "owned escape hatch · always-ready in-process fallback",
        "tagline": "Owned · in-process · provider key never leaves the boundary",
    },
    "echo": {
        "label": "EchoEngine", "vendor": "Agnos Proxy (owned)", "runtime": "Python (in-process)", "license": "owned",
        "stateful": False, "holds_provider_keys": False, "owned": True,
        "blast_radius": "none",
        "capability": "deterministic test upstream",
        "tagline": "Deterministic $0 in-process test upstream (not for production traffic)",
    },
}

# Every engine we run is stateless / key-free, so any of them is a safe harbor to
# evacuate to; Portkey is the recommended production default, Direct the always-ready
# in-process fallback.
SAFE_ENGINES: tuple[str, ...] = ("portkey", "direct", "bifrost", "litellm")


def meta(name: str) -> dict:
    return ENGINE_META.get(name, {
        "label": name, "vendor": "?", "runtime": "?", "license": "?",
        "stateful": None, "holds_provider_keys": None, "owned": False,
        "blast_radius": "unknown", "capability": "", "tagline": name,
    })
