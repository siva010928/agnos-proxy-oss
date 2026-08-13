"""Regenerate data/provider_catalog.yaml from the LiteLLM price dataset.

The catalog is the gateway's list of *known* models (reachability + the pricing
page reference). We source it ONLY from LiteLLM's verified dataset
(model_prices_and_context_window.json) so every model shown is real and prices
resolve by EXACT key match - no synthetic model ids, no fuzzy price guessing.

Model ids are the LiteLLM keys verbatim, so gateway/core/pricing.price_for()
resolves each one by exact lookup. Run:

    python scripts/gen_provider_catalog.py

Offline note: the price file is baked at data/model_prices.json (VPN-safe); this
script never hits the network.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRICES = os.path.join(_ROOT, "data", "model_prices.json")
_OUT = os.path.join(_ROOT, "data", "provider_catalog.yaml")

# our gateway provider  ->  (label, set of litellm_provider values that map to it)
PROVIDERS: "OrderedDict[str, tuple[str, set[str]]]" = OrderedDict([
    ("anthropic", ("Anthropic", {"anthropic"})),
    ("bedrock", ("Amazon Bedrock", {"bedrock", "bedrock_converse"})),
    ("gemini", ("Google Gemini", {"gemini"})),
    ("openai", ("OpenAI", {"openai", "text-completion-openai"})),
    ("azure", ("Azure OpenAI", {"azure", "azure_ai"})),
])


def main() -> None:
    with open(_PRICES) as f:
        data = json.load(f)

    out: "OrderedDict[str, dict]" = OrderedDict()
    for prov, (label, lps) in PROVIDERS.items():
        chat: list[str] = []
        embed: list[str] = []
        for key, info in data.items():
            if not isinstance(info, dict):
                continue
            if info.get("litellm_provider") not in lps:
                continue
            # only models that actually carry a price (verified, chargeable)
            if info.get("input_cost_per_token") is None and info.get("output_cost_per_token") is None:
                continue
            mode = info.get("mode")
            if mode == "embedding":
                embed.append(key)
            elif mode in ("chat", "completion", None):
                # default unmarked chat-capable models to chat
                chat.append(key)
        out[prov] = {"label": label, "chat": sorted(set(chat)), "embedding": sorted(set(embed))}

    # hand-write the YAML so the header comment + ordering stay stable + readable
    lines = [
        "# Provider/model catalog for the Agnos Proxy.",
        "# GENERATED from data/model_prices.json (LiteLLM verified dataset) by",
        "# scripts/gen_provider_catalog.py - do not hand-edit. Every model id is a",
        "# verbatim LiteLLM key, so prices resolve by EXACT match (no synthetic ids,",
        "# no fuzzy price guessing). Regenerate: python scripts/gen_provider_catalog.py",
    ]
    for prov, spec in out.items():
        lines.append(f"{prov}:")
        lines.append(f"  label: {spec['label']}")
        lines.append("  chat:")
        for m in spec["chat"]:
            lines.append(f"  - {_q(m)}")
        lines.append("  embedding:")
        for m in spec["embedding"]:
            lines.append(f"  - {_q(m)}")
    with open(_OUT, "w") as f:
        f.write("\n".join(lines) + "\n")

    total = sum(len(s["chat"]) + len(s["embedding"]) for s in out.values())
    print(f"wrote {_OUT}: {total} verified models")
    for prov, spec in out.items():
        print(f"  {prov:10} chat={len(spec['chat']):4} embedding={len(spec['embedding']):3}")


def _q(s: str) -> str:
    """Quote a YAML scalar when it contains characters that would otherwise be
    misparsed (colons, leading special chars). LiteLLM keys contain ':' and '.'."""
    if any(c in s for c in ":#{}[],&*!|>'\"%@`") or s != s.strip():
        return '"' + s.replace('"', '\\"') + '"'
    return s


if __name__ == "__main__":
    main()
