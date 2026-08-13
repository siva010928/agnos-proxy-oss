"""CrewAI demo - CrewAI agent whose LLM points at the Agnos gateway.

CrewAI uses an OpenAI-compatible client under the hood; we only set base_url +
the workspace key. Run in an isolated venv (see demo/run_crewai.sh) so CrewAI's
own deps never touch the gateway's environment.
"""
from __future__ import annotations

import os

from crewai import LLM, Agent, Crew, Task

GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")

llm = LLM(model="openai/claude-sonnet-4-5", base_url=GW, api_key=KEY)

analyst = Agent(role="Analyst", goal="Answer crisply", backstory="Concise expert.",
                llm=llm, verbose=False)
task = Task(description="Reply with exactly: CREWAI_COMPONENT_OK",
            expected_output="CREWAI_COMPONENT_OK", agent=analyst)
crew = Crew(agents=[analyst], tasks=[task], verbose=False)

if __name__ == "__main__":
    print(f"→ CrewAI agent via {GW}")
    print("←", crew.kickoff())
