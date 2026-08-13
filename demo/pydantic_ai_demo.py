"""Pydantic AI demo - used as a *component* (OpenAI adapter) pointed at the gateway.

This is distinct from the gateway's internal DirectEngine: here Pydantic AI is
the client framework, talking OpenAI to our gateway like any other component.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")

model = OpenAIChatModel("claude-sonnet-4-5", provider=OpenAIProvider(base_url=GW, api_key=KEY))
agent = Agent(model, system_prompt="You are concise.")


async def main() -> None:
    print(f"→ Pydantic AI Agent (OpenAI adapter) via {GW}")
    r = await agent.run("Reply with exactly: PYDANTIC_AI_COMPONENT_OK")
    print("←", r.output)
    print("← usage:", r.usage())


if __name__ == "__main__":
    asyncio.run(main())
