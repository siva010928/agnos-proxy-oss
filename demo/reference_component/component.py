"""NovaTech DocForge - a REFERENCE component (fictional, generically branded).

This stands in for a real Agnos Proxy LLM-powered component (a code→doc/spec
analyzer) so we can demonstrate + sanity-test the gateway from the COMPONENT'S
point of view, using its own framework's OpenAI interface (LangChain). It mirrors
the capability surface a production component needs - chat, streaming, tool
calling, structured output, embeddings, and framework-native error handling.

The ENTIRE integration is one line + one key:

    llm = ChatOpenAI(base_url=f"{GATEWAY}/v1", api_key=WORKSPACE_KEY, model="chat")

No provider credentials, no cost-tracking code, no SDK to embed - governance,
guardrails, budgets, routing, cost attribution and tracing all happen at the
gateway boundary. The component sends only a workspace key (+ optional
X-Gateway-Component / X-Gateway-Use-Case headers for attribution).
"""
from __future__ import annotations

from dataclasses import dataclass

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.tools import tool
from pydantic import BaseModel, Field

COMPONENT_NAME = "docforge-analyzer"


# ── structured-output schema (like a component extracting a typed spec) ──
class ModuleSpec(BaseModel):
    name: str = Field(description="module or service name")
    purpose: str = Field(description="one-sentence purpose")
    risks: list[str] = Field(default_factory=list, description="notable risks or gaps")


# ── tools the component's agent can call (governed transparently) ──
@tool
def search_repository(query: str) -> str:
    """Search the source repository for modules relevant to a query."""
    return f"3 matches for '{query}': auth_service.py, user_model.py, permissions.py"


@tool
def read_file(path: str) -> str:
    """Read a source file's summary."""
    return f"{path}: defines request handlers + validation; ~180 LOC."


@dataclass
class DocForge:
    """A reference component wired to the Agnos Proxy via base_url+key."""
    gateway_url: str
    workspace_key: str
    chat_alias: str = "chat"
    embed_alias: str = "embed"

    def _llm(self, use_case: str, **kw) -> ChatOpenAI:
        # A fresh client per use-case so each capability attributes to its own
        # X-Gateway-Use-Case (visible in Analytics / Request Logs / the filters).
        return ChatOpenAI(
            base_url=f"{self.gateway_url}/v1", api_key=self.workspace_key,
            model=self.chat_alias, temperature=0, max_retries=0, timeout=120,
            default_headers={"X-Gateway-Component": COMPONENT_NAME,
                             "X-Gateway-Use-Case": use_case},
            **kw)

    # 1) chat - analyze code and produce a summary
    def analyze(self, code: str) -> str:
        msg = self._llm("docforge.analyze").invoke([
            ("system", "You are a concise code analyst. Answer in one sentence."),
            ("user", f"What does this code do?\n\n{code}")])
        return msg.text() if hasattr(msg, "text") else str(msg.content)

    # 2) streaming - stream a longer doc, token by token
    def stream_summary(self, code: str):
        for chunk in self._llm("docforge.summary").stream([
            ("system", "Summarize succinctly."),
            ("user", f"Summarize:\n\n{code}")]):
            yield chunk.content or ""

    # 3) tool-calling agent - investigate using tools
    def investigate(self, question: str):
        bound = self._llm("docforge.agent").bind_tools([search_repository, read_file])
        resp = bound.invoke([
            ("system", "Use the tools to investigate, then answer."),
            ("user", question)])
        return resp.tool_calls, resp.usage_metadata

    # 4) structured output - extract a typed ModuleSpec
    def extract_spec(self, text: str) -> ModuleSpec:
        structured = self._llm("docforge.structured").with_structured_output(ModuleSpec)
        return structured.invoke([
            ("system", "Extract a ModuleSpec from the description."),
            ("user", text)])

    # 5) embeddings - build a semantic index
    def index(self, texts: list[str]) -> list[list[float]]:
        emb = OpenAIEmbeddings(base_url=f"{self.gateway_url}/v1", api_key=self.workspace_key,
                               model=self.embed_alias, check_embedding_ctx_length=False,
                               default_headers={"X-Gateway-Component": COMPONENT_NAME,
                                                "X-Gateway-Use-Case": "docforge.index"})
        return emb.embed_documents(texts)
