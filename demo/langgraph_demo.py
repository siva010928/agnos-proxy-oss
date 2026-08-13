"""LangGraph demo - StateGraph + ToolNode agent, model served through the gateway.

The ONLY gateway-specific line is the ChatOpenAI base_url. The graph, tools,
and tool-execution are 100% standard LangGraph.
"""
from __future__ import annotations

import os
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")

llm = ChatOpenAI(model="claude-sonnet-4-5", base_url=GW, api_key=KEY, temperature=0)


@tool
def search_codebase(query: str) -> str:
    """Search the codebase for modules relevant to a query."""
    return f"Found: auth_service.py, user_model.py (match='{query}')"


tools = [search_codebase]
llm_with_tools = llm.bind_tools(tools)


class State(TypedDict):
    messages: Annotated[list, add_messages]


def agent(state: State):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


g = StateGraph(State)
g.add_node("agent", agent)
g.add_node("tools", ToolNode(tools))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", tools_condition)
g.add_edge("tools", "agent")
graph = g.compile()


def main() -> None:
    print(f"→ LangGraph StateGraph+ToolNode via {GW}")
    out = graph.invoke({"messages": [HumanMessage("Find the authentication module, then summarize it.")]})
    for m in out["messages"]:
        role = type(m).__name__
        content = (m.content or "")[:160] if isinstance(m.content, str) else str(m.content)[:160]
        tc = getattr(m, "tool_calls", None)
        print(f"  [{role}] {content}{' tool_calls='+str([t['name'] for t in tc]) if tc else ''}")
    print("✓ Full LangGraph agent loop ran through the gateway (tools executed locally).")


if __name__ == "__main__":
    main()
