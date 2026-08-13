"""LangChain demo - the ONE-LINE change. init_chat_model('openai:...') pointed at
the gateway; bind_tools + agent loop unchanged. No provider creds in component.

Run the gateway first, then: poetry run python demo/langchain_demo.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")

# ── THE ONLY CHANGE: point an OpenAI-compatible client at the gateway ──
llm = init_chat_model(
    "openai:claude-sonnet-4-5",          # logical alias (gateway resolves provider)
    openai_api_base=GW,
    api_key=KEY,                          # workspace key, NOT a provider key
    temperature=0,
)


@tool
def search_codebase(query: str, scope: str = "all") -> str:
    """Search the codebase for relevant modules, classes, and patterns."""
    return f"Found 3 modules for '{query}' (scope={scope}): auth_service.py, user_model.py, permissions.py"


@tool
def generate_spec_section(section_title: str, context: str) -> str:
    """Generate a specification section from gathered context."""
    return f"## {section_title}\n\nBased on: {context[:80]}..."


def main() -> None:
    print(f"→ LangChain init_chat_model → {GW}  (tool-calling through the gateway)")
    bound = llm.bind_tools([search_codebase, generate_spec_section])
    resp = bound.invoke([
        ("system", "You are a spec agent. Use tools to answer."),
        ("user", "Search the codebase for the authentication module."),
    ])
    print("← tool_calls:", resp.tool_calls)
    print("← usage_metadata:", resp.usage_metadata)


if __name__ == "__main__":
    main()
