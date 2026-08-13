import React, { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Play, Copy, Check, ChevronDown, ChevronRight, ExternalLink, RefreshCw,
  CheckCircle2, XCircle, Wrench, Info, User as UserIcon, Building2, Boxes, Layers,
  Shield, AlertTriangle, Zap, ArrowDown, Lock, X, HelpCircle,
} from 'lucide-react'
import { useCurrency } from '../lib/currency'
import { Help } from '../components/ui'

// ─── Types ───
interface WorkspaceEntry {
  workspace_id: string; display_name: string; client_id: string
  default_chat_alias: string | null; chat_models: Record<string, any>
  engine_overrides: Record<string, string | number>
  anthropic_alias?: string   // the alias to use when calling /v1/chat/completions
}
interface GovernanceEvent {
  id: string; request_id: string; workspace_id: string; client_id: string
  user: string; component: string; provider: string; model_alias: string
  model_id: string; input_tokens: number; output_tokens: number
  total_tokens: number; cost_usd: number; latency_ms: number
  status: string; event_kind: string; engine: string; stream: boolean
  created_at: string
}
interface StageTimelineEntry { id: string; started_at_ms: number; duration_ms: number; ended_at_ms: number }
interface RoutingCandidate {
  provider: string; model_id: string; weight: number; context_window: number
  is_primary: boolean; is_selected: boolean
}
interface BudgetLevel {
  level: string; id: string; used_usd: number; limit_usd: number
  remaining_usd: number; pct_used: number; decision: 'ALLOW' | 'BLOCK'
}
interface RateLimitLevel {
  level: string; current: number; limit: number; remaining: number; decision: string
}
interface GuardrailMatch {
  rule: string; detector: string; category: string; action: string; stage: string
  excerpt: string; severity: string; confidence: number; matched_at: string | null
}
interface Explainability {
  request_id: string
  stage_timeline: StageTimelineEntry[]
  auth: { workspace_id: string; client_id: string | null; user_id: string; auth_method: string }
  routing: { alias: string; candidates: RoutingCandidate[]; selected: any; reason: string }
  guardrails: {
    mode: string
    enabled_detectors: { detector: string; matches: string[] }[]
    custom_rule_ids: string[]
    matches: GuardrailMatch[]
    decision: 'passed' | 'blocked' | 'redacted'
  }
  rate_limit: { window: string; levels: RateLimitLevel[]; overall_decision: string }
  budget: { month_start: string; levels: BudgetLevel[]; overall_decision: string }
  governance_event_json: Record<string, any>
}
interface ToolCall {
  id: string; type: string; name: string; arguments: string; duration_ms: number | null
  result?: string; _synthesized?: boolean
}
interface RunResult {
  response_text: string; framework: string; engine_used: string
  governance_event: GovernanceEvent | null; latency_ms: number
  error: string | null
  error_category: string | null; error_message: string | null
  error_details: Record<string, any> | null
  failure_stage: string | null; http_status: number
  trace_id: string | null; code_snippet: string
  tool_calls: ToolCall[]
  explainability: Explainability | null
}

// ─── Constants ───
const FRAMEWORKS = [
  { id: 'openai', label: 'OpenAI SDK' },
  { id: 'langchain', label: 'LangChain (init_chat_model)' },
  { id: 'langgraph', label: 'LangGraph' },
  { id: 'crewai', label: 'CrewAI' },
  { id: 'pydantic_ai', label: 'Pydantic AI' },
]

interface ScenarioDef {
  id: string; label: string; tagline: string; demonstrates: string
  prompt: string; icon: any; color: string
  category: 'success' | 'guardrails' | 'failures'
}

// Maps each scenario to the use_case the gateway will record - MUST match the
// backend mapping in gateway/routes/playground.py so the code snippet shows the
// exact value that ends up in the governance event.
const USE_CASE_BY_SCENARIO: Record<string, string> = {
  normal: 'playground.demo',
  tool_call: 'playground.tool_demo',
  guardrail_aws_secret: 'playground.guardrail_demo',
  guardrail_phone: 'playground.guardrail_demo',
  guardrail_email: 'playground.guardrail_demo',
  guardrail_ssn: 'playground.guardrail_demo',
  rate_limit: 'playground.rate_limit_demo',
  provider_failure: 'playground.routing_demo',
}

const SCENARIOS: ScenarioDef[] = [
  {
    id: 'normal', label: '🟢 Normal Request',
    tagline: 'A plain chat call',
    demonstrates: 'Auth · Routing · Engine · Provider · Cost attribution end to end',
    prompt: 'Explain what an API gateway does in two sentences.',
    icon: Play, color: 'accent', category: 'success',
  },
  {
    id: 'tool_call', label: '🛠 Tool Calling',
    tagline: 'Ask the model to call a tool',
    demonstrates: 'Gateway carries OpenAI tools definition through to provider unchanged',
    prompt: "What's the weather in Bangalore?",
    icon: Wrench, color: 'blue', category: 'success',
  },
  {
    id: 'guardrail_aws_secret', label: '🔒 AWS Secret',
    tagline: 'Paste an AWS access key',
    demonstrates: 'Secrets detector blocks at gateway (422) - provider never called, key never leaves',
    prompt: "Here's our AWS key: AKIAIOSFODNN7EXAMPLE for the deployment.",
    icon: Shield, color: 'red', category: 'guardrails',
  },
  {
    id: 'guardrail_phone', label: '🔒 Phone Number',
    tagline: 'Include a US phone number',
    demonstrates: 'PII detector catches it before the request leaves the boundary',
    prompt: 'My phone number is (415) 555-2671 - please remember it for next time.',
    icon: Shield, color: 'red', category: 'guardrails',
  },
  {
    id: 'guardrail_email', label: '🔒 Email',
    tagline: 'Include an email address',
    demonstrates: 'Same PII detector, different pattern',
    prompt: 'Email me the answer at test@gmail.com when you are done.',
    icon: Shield, color: 'red', category: 'guardrails',
  },
  {
    id: 'guardrail_ssn', label: '🔒 SSN',
    tagline: 'Include a US Social Security Number',
    demonstrates: 'Blocked or redacted before any spend',
    prompt: 'My SSN is 123-45-6789 for verification.',
    icon: Shield, color: 'red', category: 'guardrails',
  },
  {
    id: 'rate_limit', label: '⚡ Rate Limit',
    tagline: 'Fire a rapid burst',
    demonstrates: 'Workspace RPM cap returns 429 before a single provider call costs anything',
    prompt: 'A normal request - but preceded by a burst.',
    icon: AlertTriangle, color: 'orange', category: 'failures',
  },
  {
    id: 'provider_failure', label: '❌ Provider Failure',
    tagline: 'Ask for a model that is not configured',
    demonstrates: 'Routing fails cleanly (no candidate) - no half-call, no orphaned spend',
    prompt: 'Same prompt - but routed to a model that does not exist.',
    icon: AlertTriangle, color: 'orange', category: 'failures',
  },
]

const STAGE_INFO: Record<string, { label: string; desc: string }> = {
  auth: { label: 'Auth', desc: 'Workspace key (SHA-256) resolved to client + workspace + user' },
  routing: { label: 'Routing', desc: 'Alias resolved to a primary + fallback target list; one target selected' },
  guardrails: { label: 'Guardrails', desc: 'CEL rules + detectors evaluated against the input' },
  rate_limit: { label: 'Rate Limit', desc: 'RPM/TPM checked, innermost-first: user → workspace → client → model' },
  budget: { label: 'Budget', desc: 'Spend checked at user → workspace → client' },
  engine: { label: 'Engine', desc: 'The (swappable) engine prepared the provider request with decrypted creds' },
  provider: { label: 'Provider', desc: 'Upstream model invoked; tokens and cost computed' },
  governance: { label: 'Governance', desc: 'One event published to the bus → every observer (dashboards, billing, audit)' },
}

const ERROR_CATEGORIES: Record<string, { title: string; color: string; help: string }> = {
  model_not_found: { title: 'Model Not Available', color: 'red', help: 'Model not registered for this workspace.' },
  guardrail: { title: 'Guardrail Blocked', color: 'red', help: 'Sensitive content detected. The gateway prevented the request from reaching the provider.' },
  rate_limit: { title: 'Rate Limit Exceeded', color: 'orange', help: 'Too many requests in the trailing window.' },
  budget: { title: 'Budget Exceeded', color: 'orange', help: 'Spending cap reached at one or more levels.' },
  auth: { title: 'Authentication Failed', color: 'red', help: 'API key invalid, expired, or lacks role.' },
  validation: { title: 'Invalid Request', color: 'yellow', help: 'Validation failed.' },
  provider_error: { title: 'Provider Unavailable', color: 'orange', help: 'Upstream returned 5xx.' },
  unknown: { title: 'Unexpected Error', color: 'red', help: 'See raw details.' },
}

const DEMO_MODEL = 'claude-sonnet-4-5'   // workspace alias
const DEMO_MODEL_LABEL = 'Claude Sonnet 4.5'

// First-visit onboarding walkthrough - told as a story so a first-time viewer "gets it"
// in 90 seconds. Each step has a scene label, a headline, and the narrative.
interface TourStep { scene: string; title: string; body: string }
const TOUR_STEPS: TourStep[] = [
  {
    scene: 'THE SETUP',
    title: 'Welcome to Agnos - the LLM governance layer',
    body: "Picture an enterprise running 20 AI features across 8 teams. Every team wired its own LLM access: its own API keys, its own retry logic, its own (half-built) PII filter, its own cost tracking - and a brand-new integration each time a provider is added. That's 20 copies of the same plumbing, 20 places a key can leak, and zero consistent view of spend or policy. Agnos replaces all of that with one governed boundary. This playground is a live simulator of that boundary - every number you'll see is real, computed by the actual gateway.",
  },
  {
    scene: 'ACT 1 - WHO IS CALLING',
    title: 'Identity: a workspace key, not provider credentials',
    body: "A component never holds an OpenAI or Anthropic key. It holds one Agnos workspace key and sends its user + component as headers (resolved from its own JWT). The gateway turns that single key into a full identity - Client → Workspace → User → Component - and that identity drives every downstream decision: which provider to route to, which guardrails apply, whose budget to charge. The CONTEXT card at the top lets you impersonate different callers to see this live.",
  },
  {
    scene: 'ACT 2 - ONE LINE TO ONBOARD',
    title: 'Any framework, one base_url change',
    body: "Pick a framework - OpenAI SDK, LangChain, LangGraph, CrewAI, Pydantic AI. The code panel shows exactly what a real component writes. The ONLY change to adopt Agnos is the base_url. Everything else is that framework's ordinary code. No SDK to learn, no vendor lock-in. That's the entire onboarding cost: one line.",
  },
  {
    scene: 'ACT 3 - WATCH GOVERNANCE HAPPEN',
    title: 'Pick a scenario, hit Run, watch the pipeline',
    body: "Scenarios are grouped by what they prove - Success (normal call, tool calling), Guardrails (PII / secrets blocked at the boundary), and Failures (rate-limit, routing). Hit Run and the right side fills with the response, then the Governance Trace lights up stage by stage: Auth → Routing → Guardrails → Rate Limit → Budget → Engine → Provider → Governance. Click any stage to inspect the REAL data that decision was made on - the matched guardrail rule, the live budget number, the routing candidates.",
  },
  {
    scene: 'THE PAYOFF',
    title: 'Own the governance, rent only the translation',
    body: "Everything left of the engine boundary - auth, routing, guardrails, budgets, attribution - is ours and never changes. The only swappable part is the provider-translation engine (Bifrost or our own adapter). Flip it and the component's request and response are byte-identical. That's the difference between leverage and lock-in. One request just produced one governed, attributed event that flows to billing, audit, and analytics. Ready? Pick a scenario and hit Run.",
  },
]


export function Playground() {
  const { symbol, currency: currencyCode, rate } = useCurrency()

  const [framework, setFramework] = useState('openai')
  const [workspaces, setWorkspaces] = useState<WorkspaceEntry[]>([])
  const [selectedClient, setSelectedClient] = useState('')
  const [selectedWs, setSelectedWs] = useState('')
  const [scenario, setScenario] = useState('normal')
  const [prompt, setPrompt] = useState(SCENARIOS[0].prompt)
  // Onboarding walkthrough - shown on first visit only (localStorage flag).
  const [tourStep, setTourStep] = useState<number | null>(() => {
    if (typeof window === 'undefined') return null
    return localStorage.getItem('agnos_playground_tour_done') ? null : 0
  })
  const closeTour = () => {
    localStorage.setItem('agnos_playground_tour_done', '1')
    setTourStep(null)
  }
  const nextTourStep = () => setTourStep(s => (s !== null && s < TOUR_STEPS.length - 1 ? s + 1 : (closeTour(), null)))
  const [engine, setEngine] = useState('bifrost')
  const [maxTokens, setMaxTokens] = useState(200)
  const [result, setResult] = useState<RunResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showRawError, setShowRawError] = useState(false)
  const [showEventJson, setShowEventJson] = useState(false)
  const [showInternals, setShowInternals] = useState(false)
  const [showWhy, setShowWhy] = useState(false)
  const [expandedStages, setExpandedStages] = useState<Set<string>>(new Set())
  const [stageProgress, setStageProgress] = useState<Record<string, 'pending' | 'active' | 'done' | 'failed'>>({})
  const [runStartTime, setRunStartTime] = useState<number | null>(null)
  const [runElapsed, setRunElapsed] = useState(0)

  // Live elapsed timer while running
  useEffect(() => {
    if (loading && runStartTime) {
      const interval = setInterval(() => setRunElapsed((Date.now() - runStartTime) / 1000), 50)
      return () => clearInterval(interval)
    }
  }, [loading, runStartTime])

  // Load workspaces (no need for models - we lock to Claude 4.5)
  useEffect(() => {
    fetch('/playground/workspaces', { credentials: 'include' })
      .then(r => r.json()).then(d => {
        const ws = (d.workspaces || [])
        setWorkspaces(ws)
        if (ws.length) {
          const primaryWs = ws.find((w: WorkspaceEntry) => w.client_id === 'novatech') || ws[0]
          setSelectedWs(primaryWs.workspace_id); setSelectedClient(primaryWs.client_id)
        }
      }).catch(() => {})
  }, [])

  // Scenario change → update prompt
  const pickScenario = (s: ScenarioDef) => {
    setScenario(s.id); setPrompt(s.prompt)
  }

  const clients = [...new Set(workspaces.map(w => w.client_id))]
  const filteredWorkspaces = selectedClient ? workspaces.filter(w => w.client_id === selectedClient) : workspaces
  const currentWs = workspaces.find(w => w.workspace_id === selectedWs)

  const animateStagesFromTimeline = useCallback(async (
    stages: StageTimelineEntry[], failureStage: string | null
  ) => {
    const all = ['auth', 'routing', 'guardrails', 'rate_limit', 'budget', 'engine', 'provider', 'governance']
    setStageProgress(Object.fromEntries(all.map(id => [id, 'pending' as const])))
    for (const stage of stages) {
      setStageProgress(prev => ({ ...prev, [stage.id]: 'active' }))
      await new Promise(r => setTimeout(r, 250))
      const isFailed = stage.id === failureStage
      setStageProgress(prev => ({ ...prev, [stage.id]: isFailed ? 'failed' : 'done' }))
      if (isFailed) return
      await new Promise(r => setTimeout(r, 100))
    }
  }, [])

  const handleRun = async () => {
    setLoading(true); setResult(null); setStageProgress({})
    setShowRawError(false); setShowEventJson(false); setExpandedStages(new Set())
    setRunStartTime(Date.now()); setRunElapsed(0)

    const useTools = scenario === 'tool_call'
    // Resolve the alias dynamically per workspace so we don't hardcode an alias
    // slug that might not exist on this workspace's chat_models config.
    const wsAlias = currentWs?.anthropic_alias || currentWs?.default_chat_alias || DEMO_MODEL
    // For provider_failure scenario, override with a non-existent model
    const modelForRequest = scenario === 'provider_failure' ? 'nonexistent-model-2099' : wsAlias

    try {
      const resp = await fetch('/playground/run', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workspace_id: selectedWs, model: modelForRequest,
          prompt, framework, stream: false, use_tools: useTools,
          engine, max_tokens: maxTokens, scenario,
        }),
      })
      const data = await resp.json()
      setResult(data as RunResult)
      const stages = data.explainability?.stage_timeline || []
      animateStagesFromTimeline(stages, data.failure_stage || null)
    } catch (e: any) {
      setResult({
        response_text: e.message, framework, engine_used: engine,
        governance_event: null, latency_ms: 0, error: e.message,
        error_category: 'unknown', error_message: e.message,
        error_details: null, failure_stage: 'engine', http_status: 0,
        trace_id: null, code_snippet: '', tool_calls: [], explainability: null,
      })
    } finally {
      setLoading(false)
    }
  }

  const toggleStage = (id: string) => {
    setExpandedStages(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  // Code snippet - uses unified init_chat_model where possible
  const getCodeSnippet = () => {
    const base = window.location.origin
    // Components don't need to know the model: "default" resolves to the
    // workspace's default_chat_alias. (You can also pass an explicit alias.)
    const model = 'default'
    const key = 'gw-ws-novatech-xxxxxxxxxxxx'
    const p = prompt.length > 60 ? prompt.slice(0, 60) + '...' : prompt
    const useTools = scenario === 'tool_call'
    const useCase = USE_CASE_BY_SCENARIO[scenario] || 'playground.demo'

    const toolBlock = useTools ? `,
    tools=[{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }],
    tool_choice="required"` : ''

    const lcTools = useTools ? `

@tool
def get_weather(location: str) -> str:
    """Get current weather for a location."""
    return f"Weather in {location}: sunny"

llm = llm.bind_tools([get_weather])` : ''

    const snippets: Record<string, string> = {
      openai: `from openai import OpenAI

client = OpenAI(
    base_url="${base}/v1",
    api_key="${key}",
)

response = client.chat.completions.create(
    model="${model}",   # "default" → workspace default model (or pass an alias)
    messages=[{"role": "user", "content": "${p}"}],
    max_tokens=${maxTokens},
    metadata={"use_case": "${useCase}"},
    extra_headers={
        "X-Gateway-Component": "playground",
        "X-Gateway-User": "admin",
    }${toolBlock}
)
print(response.choices[0].message.content)`,

      langchain: `from langchain.chat_models import init_chat_model${useTools ? `
from langchain_core.tools import tool` : ''}

llm = init_chat_model(
    "${model}",            # "default" → workspace default model
    model_provider="openai",
    base_url="${base}/v1",
    api_key="${key}",
    default_headers={
        "X-Gateway-Component": "playground",
        "X-Gateway-User": "admin",
        "X-Gateway-Use-Case": "${useCase}",
    },
    max_tokens=${maxTokens},
)${lcTools}

response = llm.invoke("${p}")
print(response.content)`,

      langgraph: `from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, MessagesState, START, END

llm = init_chat_model(
    "${model}",            # "default" → workspace default model
    model_provider="openai",
    base_url="${base}/v1",
    api_key="${key}",
    default_headers={
        "X-Gateway-Component": "playground",
        "X-Gateway-User": "admin",
        "X-Gateway-Use-Case": "${useCase}",
    },
)${lcTools}

def agent(state: MessagesState):
    return {"messages": [llm.invoke(state["messages"])]}

graph = StateGraph(MessagesState)
graph.add_node("agent", agent)
graph.add_edge(START, "agent")
graph.add_edge("agent", END)
app = graph.compile()
result = app.invoke({"messages": [{"role": "user", "content": "${p}"}]})`,

      crewai: `from crewai import Agent, Task, Crew
import os

os.environ["OPENAI_API_BASE"] = "${base}/v1"
os.environ["OPENAI_API_KEY"] = "${key}"
os.environ["OPENAI_MODEL_NAME"] = "${model}"   # "default" → workspace default
os.environ["OPENAI_DEFAULT_HEADERS"] = (
    '{"X-Gateway-Component": "playground", '
    '"X-Gateway-User": "admin", '
    '"X-Gateway-Use-Case": "${useCase}"}'
)

agent = Agent(
    role="assistant",
    goal="Answer questions",
    backstory="Helpful AI"${useTools ? `,
    tools=[]` : ''}
)
task = Task(description="${p}", agent=agent, expected_output="Helpful response")
crew = Crew(agents=[agent], tasks=[task])
result = crew.kickoff()`,

      pydantic_ai: `from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(
    base_url="${base}/v1",
    api_key="${key}",
)
model = OpenAIModel(
    "${model}",            # "default" → workspace default model
    provider=provider,
    extra_headers={
        "X-Gateway-Component": "playground",
        "X-Gateway-User": "admin",
        "X-Gateway-Use-Case": "${useCase}",
    },
)
agent = Agent(model)${useTools ? `

@agent.tool_plain
def get_weather(location: str) -> str:
    """Get current weather."""
    return f"Weather in {location}: sunny"` : ''}

result = agent.run_sync("${p}")
print(result.data)`,
    }
    return snippets[framework] || snippets.openai
  }

  const copyCode = () => {
    navigator.clipboard.writeText(getCodeSnippet())
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }

  // Code panel - rendered in the RIGHT column (stacked above the Response) so the
  // loop reads naturally: change a setting on the left → code updates on the
  // right → Run → output appears directly below the code.
  const codeBlock = (
    <div className="rounded-xl overflow-hidden" style={{ background: 'var(--color-code-bg)', border: '1px solid var(--color-border)' }}>
      <div className="flex items-center justify-between px-3 py-2" style={{ borderBottom: '1px solid var(--color-border)' }}>
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-accent font-semibold">{FRAMEWORKS.find(f => f.id === framework)?.label}</span>
          <span className="text-[10px]" style={{ color: 'var(--color-muted)' }}>- clean component code. Highlighted lines are the only change.</span>
        </div>
        <button onClick={copyCode} className="hover:opacity-70 transition" style={{ color: 'var(--color-muted)' }}>
          {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} />}
        </button>
      </div>
      <pre className="p-3 text-[11px] overflow-x-auto font-mono leading-relaxed max-h-[460px] overflow-y-auto" style={{ color: 'var(--color-code-text)' }}>
        {getCodeSnippet().split('\n').map((line, i) => {
          const isBaseUrl = line.includes('base_url') || line.includes('OPENAI_API_BASE')
          const isApiKey = line.includes('api_key') || line.includes('OPENAI_API_KEY')
          const isHeader = line.includes('X-Gateway-Component') || line.includes('X-Gateway-User') || line.includes('X-Gateway-Use-Case') || line.includes('OPENAI_DEFAULT_HEADERS') || line.includes('metadata') || line.includes('use_case') || line.includes('default_headers') || line.includes('extra_headers')
          const isToolLine = scenario === 'tool_call' && (
            line.includes('tools=') || line.includes('tool_choice') ||
            line.includes('@tool') || line.includes('bind_tools') ||
            line.includes('@agent.tool_plain')
          )
          return (
            <div key={i} className={
              isBaseUrl ? 'bg-accent/15 text-accent -mx-3 px-3 border-l-2 border-accent' :
              isApiKey ? 'bg-amber-500/10 text-amber-300 -mx-3 px-3 border-l-2 border-amber-400' :
              isHeader ? 'bg-violet-500/10 text-violet-300 -mx-3 px-3 border-l-2 border-violet-400' :
              isToolLine ? 'bg-blue-500/10 text-blue-300 -mx-3 px-3 border-l-2 border-blue-400' : ''
            }>{line}</div>
          )
        })}
      </pre>
      <div className="px-3 py-1.5 border-t border-border bg-surface/30 text-[9px] text-muted">
        <span className="inline-block w-2 h-0.5 bg-accent align-middle mr-1" /> base_url (gateway) ·
        <span className="inline-block w-2 h-0.5 bg-amber-400 align-middle ml-2 mr-1" /> workspace key ·
        <span className="inline-block w-2 h-0.5 bg-violet-400 align-middle ml-2 mr-1" /> identity headers ·
        {scenario === 'tool_call' && <><span className="inline-block w-2 h-0.5 bg-blue-400 align-middle ml-2 mr-1" /> tool definition ·</>}
        <span style={{ color: '#6E7890' }} className="ml-1">zero provider credentials in the component</span>
      </div>
    </div>
  )

  const isSuccess = result && !result.error_category
  const isError = result && !!result.error_category
  const errInfo = result?.error_category ? ERROR_CATEGORIES[result.error_category] || ERROR_CATEGORIES.unknown : null
  const exp = result?.explainability

  return (
    <div className="space-y-4 pb-8 max-w-[1500px] mx-auto">
      {/* Onboarding walkthrough - first visit only */}
      {tourStep !== null && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
             onClick={closeTour} data-testid="playground-tour-overlay">
          <div className="border-2 rounded-2xl p-6 max-w-lg shadow-2xl"
               style={{ background: 'var(--color-surface)', borderColor: 'var(--color-accent)' }}
               onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold px-2 py-0.5 rounded tracking-widest"
                      style={{ color: 'var(--color-accent)', background: 'var(--color-accent-soft)' }}>
                  {TOUR_STEPS[tourStep].scene}
                </span>
                <span className="text-[10px]" style={{ color: 'var(--color-text-muted)' }}>
                  {tourStep + 1} / {TOUR_STEPS.length}
                </span>
              </div>
              <button onClick={closeTour} className="hover:opacity-70" style={{ color: 'var(--color-text-muted)' }} aria-label="Skip tour">
                <X size={16} />
              </button>
            </div>
            <h3 className="text-lg font-bold mb-2" style={{ color: 'var(--color-text)' }}>{TOUR_STEPS[tourStep].title}</h3>
            <p className="text-[13px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>{TOUR_STEPS[tourStep].body}</p>
            <div className="flex items-center justify-between mt-5">
              <div className="flex gap-1">
                {TOUR_STEPS.map((_, i) => (
                  <span key={i} className="w-1.5 h-1.5 rounded-full transition-colors"
                        style={{ background: i === tourStep ? 'var(--color-accent)' : 'var(--color-border-strong)' }} />
                ))}
              </div>
              <div className="flex gap-2">
                <button onClick={closeTour} className="text-xs px-3 py-1.5 hover:opacity-70" style={{ color: 'var(--color-text-muted)' }}>
                  Skip
                </button>
                <button onClick={nextTourStep}
                        className="text-xs bg-accent text-black font-medium px-4 py-1.5 rounded hover:bg-accent/90 transition"
                        data-testid="tour-next">
                  {tourStep === TOUR_STEPS.length - 1 ? "Let's go" : 'Next'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Re-open tour button (always available) */}
      {/* HEADER - short and action-oriented */}
      <div className="bg-gradient-to-r from-accent/10 via-violet-500/10 to-blue-500/10 border border-accent/20 rounded-lg p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Layers size={22} className="text-accent flex-shrink-0" />
            <div>
              <div className="text-sm font-bold text-white">See exactly what Agnos does to an LLM request</div>
              <div className="text-[11px] text-muted mt-0.5 flex items-center gap-2 flex-wrap">
                <span className="font-bold text-accent">1.</span> Pick a scenario below
                <span className="text-border">·</span>
                <span className="font-bold text-accent">2.</span> Click Run
                <span className="text-border">·</span>
                <span className="font-bold text-accent">3.</span> Watch governance happen live
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0">
            <button onClick={() => setTourStep(0)} className="text-xs text-accent hover:underline flex items-center gap-1"
                    data-testid="replay-tour" title="Replay the onboarding walkthrough">
              <HelpCircle size={12} /> Tour
            </button>
            <button onClick={() => setShowWhy(!showWhy)} className="text-xs text-accent hover:underline flex items-center gap-1">
              {showWhy ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Why this exists
            </button>
          </div>
        </div>
        <AnimatePresence>
          {showWhy && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden mt-3">
              <div className="text-[11.5px] space-y-3 border-t pt-3" style={{ color: 'var(--color-text-secondary)', borderColor: 'var(--color-border)' }}>
                <div>
                  <div className="text-[10px] uppercase tracking-widest font-bold mb-1" style={{ color: 'var(--color-accent)' }}>The problem we set out to solve</div>
                  Enterprises ship AI into dozens of products at once, and the safety layer was usually <strong style={{ color: 'var(--color-text)' }}>optional - and often left off</strong>. Each team integrated LLMs from scratch: its own keys, its own retry/fallback logic, its own (usually incomplete) PII and secrets filtering, its own rate-limiting and cost tracking. And because every service bundled the same LLM client library, a single security advisory in that library forced a patch-and-redeploy across <em>every</em> app that embedded it. The result: <strong style={{ color: 'var(--color-text)' }}>N half-built copies of the same plumbing</strong>, N places a key can leak, inconsistent policy, and one shared dependency sitting on everyone's critical path.
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest font-bold mb-1" style={{ color: 'var(--color-accent)' }}>Our solution</div>
                  <strong style={{ color: 'var(--color-text)' }}>One OpenAI-compatible governance boundary</strong> that every component talks to. A component changes a single line - its <code className="text-accent">base_url</code> - and authenticates with a workspace key (never a provider key). From that moment it inherits, for free: centralized credential isolation, alias→provider routing with weighted load-balancing and fallback, CEL-based guardrails (PII / secrets / custom rules), hierarchical rate-limits and budgets (client→workspace→user), multi-currency cost attribution, full observability (metrics, traces, an event bus), and a swappable provider-translation engine. In whatever framework the team already uses.
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest font-bold mb-1" style={{ color: 'var(--color-accent)' }}>What it solves, concretely</div>
                  <ul className="list-disc ml-4 space-y-0.5">
                    <li><strong style={{ color: 'var(--color-text)' }}>Security:</strong> components never hold provider keys - credentials live encrypted in one place, decrypted only at the boundary.</li>
                    <li><strong style={{ color: 'var(--color-text)' }}>Consistency:</strong> one guardrail/budget/rate-limit policy applies everywhere, not N divergent copies.</li>
                    <li><strong style={{ color: 'var(--color-text)' }}>Visibility:</strong> every call lands as one attributed governance event - exact cost per client/workspace/user/component/use-case.</li>
                    <li><strong style={{ color: 'var(--color-text)' }}>Velocity:</strong> onboard a new component or provider with a one-line change, not an N-week integration.</li>
                    <li><strong style={{ color: 'var(--color-text)' }}>No lock-in:</strong> we own the governance; we only <em>rent</em> provider translation - and it's swappable mid-flight.</li>
                  </ul>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-widest font-bold mb-1" style={{ color: 'var(--color-accent)' }}>What this page proves</div>
                  The same request runs through five different frameworks (OpenAI SDK, LangChain, LangGraph, CrewAI, Pydantic AI) and lands as one governed, attributed event. Swap the engine (rented Bifrost ⇄ our own adapter) and the component's request and response are <strong style={{ color: 'var(--color-text)' }}>byte-identical</strong> - only the translation behind the boundary moves. Every number on this page is computed by the real gateway, not mocked.
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* STEP 0 - Request Context (formerly "impersonating a component") */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
        <div className="md:col-span-3 bg-elevated rounded-lg p-3 border border-border">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] font-bold text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">CONTEXT</span>
            <UserIcon size={14} className="text-violet-400" />
            <span className="text-[11px] uppercase tracking-wide text-white font-semibold">Who is making this request?</span>
            <Help side="bottom" text={
              <span>Every call carries a full identity: <strong>Client → Workspace → User → Component</strong>. The gateway derives it from one workspace key plus request headers - the component never holds a provider key. This identity drives every downstream decision: which provider to route to, which guardrails apply, and whose budget to charge. Impersonate different callers here to see routing, limits and attribution change live.</span>
            } />
            <button onClick={() => setShowInternals(!showInternals)}
              className="ml-auto text-[10px] text-muted hover:text-white flex items-center gap-1">
              <Info size={10} /> what does this mean?
            </button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
            <IdField icon={<Building2 size={11} />} label="Client" value={
              <select value={selectedClient} onChange={e => {
                setSelectedClient(e.target.value)
                const first = workspaces.find(w => w.client_id === e.target.value)
                if (first) setSelectedWs(first.workspace_id)
              }} className="bg-surface border border-border rounded px-2 py-1 text-[11px] text-white w-full">
                {clients.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            } />
            <IdField icon={<Boxes size={11} />} label="Workspace" value={
              <select value={selectedWs} onChange={e => setSelectedWs(e.target.value)}
                className="bg-surface border border-border rounded px-2 py-1 text-[11px] text-white w-full">
                {filteredWorkspaces.map(ws => <option key={ws.workspace_id} value={ws.workspace_id}>{ws.workspace_id}</option>)}
              </select>
            } />
            <IdField icon={<UserIcon size={11} />} label="User (JWT sub)" readOnly hint="Derived from the caller's JWT 'sub' claim - sent as a header, not editable here."
              value={<span className="font-mono text-white block bg-surface/50 border border-border/60 rounded px-2 py-1">admin</span>} />
            <IdField icon={<Layers size={11} />} label="Component" readOnly hint="The calling component name (X-Gateway-Component header) - fixed to 'playground' here."
              value={<span className="font-mono text-white block bg-surface/50 border border-border/60 rounded px-2 py-1">playground</span>} />
          </div>

          {/* Generated headers preview - the exact wire identity the gateway sees */}
          <div className="mt-2 rounded-lg p-2.5 font-mono text-[10.5px] leading-relaxed"
               style={{ background: 'var(--color-code-bg)', border: '1px solid var(--color-border)' }}
               data-testid="pg-headers-preview">
            <div className="text-[9px] uppercase tracking-wider text-muted mb-1 not-italic" style={{ fontFamily: 'inherit' }}>Generated headers preview</div>
            <div style={{ color: 'var(--color-code-text)' }}>X-Gateway-Client: <span className="text-violet-300">{selectedClient || '-'}</span></div>
            <div style={{ color: 'var(--color-code-text)' }}>X-Gateway-User: <span className="text-violet-300">admin</span></div>
            <div style={{ color: 'var(--color-code-text)' }}>X-Gateway-Component: <span className="text-violet-300">playground</span></div>
            <div style={{ color: 'var(--color-code-text)' }}>Authorization: <span className="text-amber-300">Bearer gw-ws-{(selectedWs || 'workspace').slice(0, 18)}··········</span></div>
          </div>
          <AnimatePresence>
            {showInternals && (
              <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden">
                <div className="mt-2 pt-2 border-t border-border/50 text-[10.5px] text-gray-300 space-y-2">
                  <div className="font-bold text-white text-[11px]">How identity flows from a real component to the gateway:</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="bg-surface rounded-lg p-2.5 border border-border/50">
                      <div className="text-[10px] uppercase tracking-wide text-accent font-bold mb-1">What the component sends</div>
                      <div className="space-y-1 text-[10.5px]">
                        <div><code className="text-accent">Authorization: Bearer gw-ws-...</code></div>
                        <div className="text-muted ml-3">The <strong className="text-white">workspace key</strong> - issued in Admin. This single credential tells the gateway which Client + Workspace the call belongs to.</div>
                        <div><code className="text-violet-300">X-Gateway-User: alice@novatech</code></div>
                        <div className="text-muted ml-3">The <strong className="text-white">user identity</strong> - typically the JWT <code>sub</code> claim from the component's own auth system. Enables per-user rate limits + budget.</div>
                        <div><code className="text-violet-300">X-Gateway-Component: billing-agent</code></div>
                        <div className="text-muted ml-3">The <strong className="text-white">component name</strong> - which app is calling. Auto-registered; appears in analytics + cost attribution.</div>
                      </div>
                    </div>
                    <div className="bg-surface rounded-lg p-2.5 border border-border/50">
                      <div className="text-[10px] uppercase tracking-wide text-green-400 font-bold mb-1">What the gateway resolves internally</div>
                      <div className="space-y-1 text-[10.5px]">
                        <div>SHA-256(key) → <strong className="text-white">Workspace</strong> (config, routing, guardrails, budgets)</div>
                        <div>Workspace.client_id → <strong className="text-white">Client</strong> (enterprise org, cross-workspace cap)</div>
                        <div>Fernet decrypt → <strong className="text-white">Provider credentials</strong> (never exposed to the component)</div>
                        <div>Headers → <strong className="text-white">User + Component</strong> (attribution labels on all events)</div>
                      </div>
                      <div className="text-[9.5px] text-muted mt-2 border-t border-border/30 pt-1.5">
                        The component never knows the provider key, never picks a provider - it just sends a prompt to the gateway and gets a governed response. All routing, credential management, and cost attribution happen behind the workspace key.
                      </div>
                    </div>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* TARGET (model + engine) - what's actually being tested */}
        <div className="md:col-span-2 bg-elevated rounded-lg p-3 border border-border">
          <div className="flex items-center gap-2 mb-2">
            <Zap size={14} className="text-accent" />
            <span className="text-[11px] uppercase tracking-wide text-accent font-semibold">Target</span>
            <Help side="bottom" text={
              <span>The model alias and the <strong>engine</strong> that translates the request to the provider. The engine is swappable (Bifrost ⇄ our own adapter) with zero change to the component or to governance - flip it and the request and the reply are byte-identical. That's the difference between leverage and lock-in.</span>
            } />
          </div>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>
              <div className="text-[10px] text-muted mb-0.5">Model</div>
              <div className="bg-surface border border-border rounded px-2 py-1 flex items-center gap-1.5">
                <Lock size={10} className="text-muted" />
                <span className="font-mono text-white text-[11px]">{DEMO_MODEL_LABEL}</span>
              </div>
            </div>
            <div>
              <div className="text-[10px] text-muted mb-0.5">Engine</div>
              {/* Engine routing is GATEWAY-WIDE. Show a read-only badge when the gateway
                  pins Anthropic to a specific engine (owned/split); '' means rented, so
                  the demo can still switch engines freely to prove the boundary. */}
              {currentWs?.engine_overrides?.anthropic ? (
                <div className="bg-violet-500/10 border border-violet-500/30 rounded px-2 py-1 flex items-center gap-1.5">
                  <Lock size={10} className="text-violet-400" />
                  <span className="text-violet-300 text-[11px] font-medium">
                    {currentWs.engine_overrides.anthropic === 'direct' ? 'Owned (Direct)' : 'Split (canary)'}
                    <span className="text-[9px] text-muted"> (gateway policy)</span>
                  </span>
                </div>
              ) : (
                <select value={engine} onChange={e => setEngine(e.target.value)}
                  className="w-full bg-surface border border-border rounded px-2 py-1 text-[11px] text-white"
                  data-testid="pg-engine-select">
                  <option value="bifrost">Bifrost (Go sidecar · stateless)</option>
                  <option value="litellm">LiteLLM (proxy · stateless)</option>
                  <option value="portkey">Portkey (OSS · stateless)</option>
                  <option value="direct">Direct (in-process · ours)</option>
                  <option value="echo">Echo (deterministic · $0)</option>
                </select>
              )}
            </div>
          </div>
          <div className="text-[9px] text-muted mt-1">
            {currentWs?.engine_overrides?.anthropic
              ? 'This gateway pins Anthropic to a specific engine (gateway-wide admin policy). The governance pipeline is identical - only the translation engine differs.'
              : 'Switch engine to prove the boundary is swappable. Same request, same governance, different translation layer.'}
          </div>
        </div>
      </div>

      {/* STEP 1 - SCENARIO PICKER (grouped) */}
      <div className="bg-elevated rounded-lg p-3 border border-border">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-accent bg-accent/10 px-2 py-0.5 rounded">STEP 1</span>
            <span className="text-[11px] uppercase tracking-wide text-white font-semibold">Pick a scenario</span>
            <Help side="bottom" text={
              <span>Scenarios are grouped by what they <strong>prove</strong>:
              <br/>• <strong>Success</strong> - a normal call and a tool-calling call complete end-to-end.
              <br/>• <strong>Guardrail</strong> - PII / secrets are detected and blocked or redacted at the boundary, before reaching the provider.
              <br/>• <strong>Failure</strong> - rate-limit and routing failures are handled gracefully.
              <br/>Each one pre-fills a realistic prompt; you can edit it. Whatever is in the box is sent verbatim - the gateway never mutates your input.</span>
            } />
          </div>
          <span className="text-[10px] text-muted">
            Pre-fills the prompt with a realistic example - or type your own. Whatever's in the box is what gets sent.
          </span>
        </div>
        {(['success', 'guardrails', 'failures'] as const).map(cat => {
          const items = SCENARIOS.filter(s => s.category === cat)
          const heading = cat === 'success' ? 'Success scenarios' :
                          cat === 'guardrails' ? 'Guardrail scenarios' :
                          'Failure scenarios'
          const sub = cat === 'success' ? 'happy-path calls that complete end-to-end' :
                      cat === 'guardrails' ? 'sensitive content blocked / redacted at the boundary' :
                      'degraded providers · quota · routing failures'
          const dot = cat === 'success' ? '#34D399' : cat === 'guardrails' ? '#FBBF24' : '#F87171'
          return (
            <div key={cat} className="mb-3 last:mb-0">
              <div className="flex items-center gap-2 mb-2">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dot }} />
                <span className="text-[12px] font-semibold" style={{ color: 'var(--color-text)' }}>{heading}</span>
                <span className="text-[10.5px]" style={{ color: 'var(--color-text-secondary)' }}>· {sub}</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {items.map(s => {
                  const Icon = s.icon
                  const active = scenario === s.id
                  return (
                    <button key={s.id} onClick={() => pickScenario(s)}
                      className={`text-left rounded-lg p-2.5 border-2 transition ${
                        active ?
                          (s.color === 'red' ? 'border-red-400/60 bg-red-500/10' :
                           s.color === 'orange' ? 'border-orange-400/60 bg-orange-500/10' :
                           s.color === 'blue' ? 'border-blue-400/60 bg-blue-500/10' :
                           'border-accent/60 bg-accent/10') : 'border-border bg-surface hover:border-accent/40'
                      }`}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <Icon size={12} className={
                          s.color === 'red' ? 'text-red-400' :
                          s.color === 'orange' ? 'text-orange-400' :
                          s.color === 'blue' ? 'text-blue-400' :
                          'text-accent'
                        } />
                        <span className="text-[11px] font-semibold text-white">{s.label}</span>
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--color-text-secondary)' }}>{s.tagline}</div>
                      <div className="text-[9.5px] mt-1" style={{ color: 'var(--color-muted)' }}>{s.demonstrates}</div>
                    </button>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* TWO-COLUMN: PROMPT + CODE  vs  RESULT */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* LEFT: Framework + Prompt + Code */}
        <div className="space-y-3">
          {/* STEP 2 - Framework chips */}
          <div className="bg-elevated rounded-lg p-3 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-bold text-accent bg-accent/10 px-2 py-0.5 rounded">STEP 2</span>
              <label className="text-[10px] text-muted uppercase tracking-wide">Framework</label>
              <Help side="bottom" text={
                <span>The same governed request, expressed in five popular stacks (OpenAI SDK, LangChain, LangGraph, CrewAI, Pydantic AI). Notice that the <strong>only</strong> line that changes to adopt Agnos is the <code>base_url</code> - there is no SDK to learn and no vendor lock-in. That single line is the entire onboarding cost.</span>
              } />
            </div>
            <div className="text-[10px] text-muted mb-2">Choose the framework your component is built in. In every single one, the only change is the base_url - everything else is that framework's ordinary code.</div>
            <div className="flex flex-wrap gap-1.5">
              {FRAMEWORKS.map(f => (
                <button key={f.id} onClick={() => setFramework(f.id)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition ${
                    framework === f.id ? 'bg-accent text-black' : 'bg-surface text-muted hover:text-white border border-border'
                  }`}>{f.label}</button>
              ))}
            </div>
          </div>

          {/* STEP 3 - Prompt + Run */}
          <div className="bg-elevated rounded-lg p-3 border border-border">
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-bold text-accent bg-accent/10 px-2 py-0.5 rounded">STEP 3</span>
                <label className="text-[10px] text-muted uppercase tracking-wide">Prompt (sent verbatim)</label>
              </div>
              <span className="text-[9px] text-muted">scenario: <span className="text-white">{scenario}</span></span>
            </div>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={3}
              className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white resize-none focus:ring-1 focus:ring-accent focus:border-accent"
              placeholder="Enter your prompt..." />
            <div className="text-[9px] text-muted mt-1">
              Guardrails are <strong className="text-white">always on</strong> for this workspace - they fire on any PII/secret content, regardless of which scenario is selected.
              {scenario === 'tool_call' && <> · Tool Calling sets <code className="text-blue-300">tool_choice="required"</code>: the model is forced to call a tool whatever you ask.</>}
              {scenario === 'rate_limit' && <> · Rate Limit drains the workspace's configured RPM budget, then fires your request (expect 429).</>}
              {scenario === 'provider_failure' && <> · Provider Failure routes to a non-existent model id (returns 404 at routing).</>}
            </div>
            <div className="flex items-center gap-2 mt-2">
              <button onClick={handleRun} disabled={loading || !selectedWs}
                className="flex-1 py-3 bg-accent hover:bg-accent/90 text-black font-bold rounded-lg transition flex items-center justify-center gap-2 disabled:opacity-40 text-sm shadow-lg shadow-accent/20">
                {loading ? <><RefreshCw size={16} className="animate-spin" />Running... ({runElapsed.toFixed(1)}s)</>
                         : <><Play size={16} fill="currentColor" />Run</>}
              </button>
              <div className="text-[10px] text-muted px-2">
                <div>max</div>
                <input type="number" value={maxTokens} onChange={e => setMaxTokens(+e.target.value)}
                  className="w-12 bg-surface border border-border rounded px-1 py-0.5 text-[10px] text-white" />
              </div>
            </div>
          </div>

          {/* Status banner */}
          {result && (
            <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}
              className={`rounded-lg p-3 border-2 ${
                isSuccess ? 'bg-green-500/10 border-green-500/40' :
                errInfo?.color === 'orange' ? 'bg-orange-500/10 border-orange-500/40' :
                errInfo?.color === 'yellow' ? 'bg-yellow-500/10 border-yellow-500/40' :
                'bg-red-500/10 border-red-500/40'
              }`}>
              <div className="flex items-start gap-2">
                {isSuccess ? <CheckCircle2 size={20} className="text-green-400 flex-shrink-0" /> :
                  <XCircle size={20} className={
                    errInfo?.color === 'orange' ? 'text-orange-400 flex-shrink-0' :
                    errInfo?.color === 'yellow' ? 'text-yellow-400 flex-shrink-0' :
                    'text-red-400 flex-shrink-0'
                  } />}
                <div className="flex-1">
                  <div className={`text-sm font-bold ${
                    isSuccess ? 'text-green-400' :
                    errInfo?.color === 'orange' ? 'text-orange-400' :
                    errInfo?.color === 'yellow' ? 'text-yellow-400' :
                    'text-red-400'
                  }`}>{isSuccess ? `Completed in ${(result.latency_ms/1000).toFixed(1)}s` : errInfo?.title || 'Failed'}</div>
                  <div className="text-[10px] text-muted mt-1 flex flex-wrap gap-x-3">
                    {isSuccess && result.governance_event && (<>
                      <span><strong className="text-white">{result.governance_event.provider}</strong></span><span>·</span>
                      <span className="font-mono">{result.governance_event.model_id}</span><span>·</span>
                      <span className="text-accent">{result.engine_used}</span>
                    </>)}
                    {isError && (<>
                      <span>HTTP <strong className="text-white">{result.http_status}</strong></span>
                      {result.failure_stage && <><span>·</span><span>failed at <strong className="text-white">{result.failure_stage}</strong></span></>}
                    </>)}
                  </div>
                  {isError && (
                    <div className="text-[11px] mt-2 space-y-1">
                      {result.error_message && (
                        <div className="text-gray-200 font-medium">{result.error_message}</div>
                      )}
                      {errInfo && <div className="text-muted">{errInfo.help}</div>}
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          )}

          {/* Response */}
          <div className="bg-elevated rounded-lg border border-border min-h-[140px]">
            <div className="px-3 py-2 border-b border-border flex items-center justify-between">
              <span className="text-xs font-medium text-white">Response</span>
              {loading && <span className="text-[10px] text-accent flex items-center gap-1"><RefreshCw size={10} className="animate-spin" />{runElapsed.toFixed(1)}s</span>}
            </div>
            <div className="p-4">
              {!result && !loading && (
                <div className="text-center text-muted text-sm py-8">
                  <Play size={24} className="mx-auto mb-2 opacity-30" />
                  Click <strong>Run</strong> to execute through the governance pipeline
                </div>
              )}
              {loading && (
                <div className="text-center text-muted text-sm py-8">
                  <RefreshCw size={24} className="mx-auto mb-2 animate-spin opacity-50" />
                  Executing... ({runElapsed.toFixed(1)}s)
                </div>
              )}
              {result && (<>
                {isSuccess && (
                  <div className="text-sm text-gray-100 whitespace-pre-wrap font-mono leading-relaxed max-h-[260px] overflow-y-auto">
                    {result.response_text}
                  </div>
                )}
                {isError && (
                  <div className="space-y-2">
                    <div className="text-sm text-gray-200">{result.error_message}</div>
                    <button onClick={() => setShowRawError(!showRawError)} className="text-[10px] text-accent hover:underline flex items-center gap-1">
                      {showRawError ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                      {showRawError ? 'Hide' : 'Show'} raw error JSON
                    </button>
                    {showRawError && result.error_details && (
                      <pre className="text-[10px] text-gray-400 bg-surface p-2 rounded border border-border/50 overflow-x-auto font-mono">
                        {JSON.stringify(result.error_details, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </>)}
            </div>
          </div>

          {/* Governance trace - under the Response so both columns stay balanced */}
          {Object.keys(stageProgress).length > 0 && (
            <div className="bg-elevated rounded-lg p-3 border border-border">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-white flex items-center gap-1.5">
                  Governance trace - the enforcement point components don't have to build
                  <Help text={
                    <span>Each stage is a real decision the gateway made for this request: <strong>Auth → Routing → Guardrails → Rate Limit → Budget → Engine → Provider → Governance</strong>. Click any stage to inspect the actual data behind it - the matched guardrail rule, the live budget number, the routing candidates. Nothing here is mocked; it's the same pipeline every production request flows through.</span>
                  } />
                </span>
                <div className="flex gap-2">
                  <a href="/app/live" className="text-[10px] text-accent hover:underline flex items-center gap-0.5">Live Feed <ExternalLink size={8} /></a>
                  {result?.trace_id && (
                    <a href={`${window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:16686' : ''}/jaeger/trace/${result.trace_id}`} target="_blank" rel="noopener"
                       className="text-[10px] text-accent hover:underline flex items-center gap-0.5">Jaeger <ExternalLink size={8} /></a>
                  )}
                </div>
              </div>
              <div className="text-[10px] text-muted mb-2">Every step below is a real stage of this request, with its real timing. This is exactly the work each component used to reimplement on its own - now it happens once, here.</div>
              <div className="space-y-1">
                {['auth', 'routing', 'guardrails', 'rate_limit', 'budget', 'engine', 'provider', 'governance'].map((stageId) => {
                  const status = stageProgress[stageId] || 'pending'
                  const info = STAGE_INFO[stageId]
                  const timeline = exp?.stage_timeline.find(s => s.id === stageId)
                  const isExpanded = expandedStages.has(stageId)
                  const isClickable = status === 'done' || status === 'failed'
                  return (
                    <div key={stageId} className="rounded overflow-hidden">
                      <motion.button onClick={() => isClickable && toggleStage(stageId)} disabled={!isClickable}
                        initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }}
                        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs transition ${
                          status === 'done' ? 'text-green-400 hover:bg-green-500/5' :
                          status === 'active' ? 'text-accent bg-accent/10' :
                          status === 'failed' ? 'text-red-400 bg-red-500/10 border border-red-500/30 hover:bg-red-500/15' :
                          'text-muted opacity-50 cursor-default'
                        } ${isClickable ? 'cursor-pointer' : ''}`}>
                        <span className="w-4 text-center flex-shrink-0">
                          {status === 'done' && <CheckCircle2 size={12} />}
                          {status === 'active' && <span className="inline-block w-2 h-2 rounded-full bg-accent animate-pulse" />}
                          {status === 'failed' && <XCircle size={12} />}
                          {status === 'pending' && <span className="opacity-30">○</span>}
                        </span>
                        <span className="font-medium w-20 flex-shrink-0 text-left">{info?.label}</span>
                        {timeline && (
                          <span className="text-[9px] text-muted font-mono flex-shrink-0 w-24">
                            +{timeline.started_at_ms.toFixed(0)}ms · {timeline.duration_ms.toFixed(0)}ms
                          </span>
                        )}
                        <span className="text-[10px] opacity-70 truncate flex-1 text-left">{info?.desc}</span>
                        {isClickable && <span className="text-[10px] opacity-50">{isExpanded ? '▼' : '▶'}</span>}
                      </motion.button>
                      <AnimatePresence>
                        {isExpanded && (
                          <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
                            <div className="ml-6 pl-2 mt-1 border-l-2 border-border">
                              <StageDetail stageId={stageId} exp={exp} result={result} />
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )
                })}
                {result?.failure_stage && (
                  <div className="text-[10px] text-red-400/70 pl-7 italic mt-1">
                    Pipeline stopped at <strong>{result.failure_stage}</strong> - request never reached provider.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: Code (live) → result detail */}
        <div className="space-y-3">
          {codeBlock}

          {/* Cost (success only) - directly under the code block */}
          {result?.governance_event && isSuccess && (
            <div className="bg-elevated rounded-lg p-3 border border-border">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-white">Real cost & tokens for this call</span>
                <span className="text-[10px] text-muted">attributed to {result.governance_event.client_id} → {result.governance_event.workspace_id}</span>
              </div>
              <div className="text-[10px] text-muted mb-2">Measured from this request and attributed to client → workspace, shown in your selected currency.</div>
              <div className="grid grid-cols-3 gap-3 text-center">
                <Stat label="Tokens" value={`${result.governance_event.input_tokens}+${result.governance_event.output_tokens}`} sub={`${result.governance_event.total_tokens} total`} />
                <Stat label="Cost (USD)" value={`$${result.governance_event.cost_usd.toFixed(6)}`} accent />
                <Stat label={`Cost (${currencyCode})`} value={`${symbol}${(result.governance_event.cost_usd * rate).toFixed(4)}`} accent />
              </div>
            </div>
          )}

          {/* Governance Event "Published ✓" badge - inspect payload, under the code */}
          {exp?.governance_event_json && Object.keys(exp.governance_event_json).length > 0 && (
            <div className="bg-elevated rounded-lg border border-border overflow-hidden">
              <button onClick={() => setShowEventJson(!showEventJson)}
                className="w-full px-3 py-2 flex items-center justify-between hover:bg-surface/30 transition">
                <div className="flex items-center gap-2">
                  <CheckCircle2 size={12} className="text-green-400" />
                  <span className="text-xs font-medium text-white">The one event this request produced</span>
                  <span className="text-[10px] text-muted font-mono">topic: agnos-proxy.governance.v1</span>
                </div>
                <span className="text-[10px] text-accent">{showEventJson ? <ChevronDown size={12} /> : <ChevronRight size={12} />} inspect payload</span>
              </button>
              <AnimatePresence>
                {showEventJson && (
                  <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
                    <div className="text-[10px] text-muted px-3 pt-2 border-t border-border">
                      This is the single envelope the gateway publishes - the same one every downstream consumer (billing, audit, analytics) receives. Attribution is built in: client, workspace, user, component, provider, model, tokens, cost.
                    </div>
                    <pre className="text-[10px] text-gray-300 bg-[var(--color-code-bg)] p-3 font-mono overflow-x-auto">
                      {JSON.stringify(exp.governance_event_json, null, 2)}
                    </pre>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          {/* Tool Calls - prominent when present */}
          {result?.tool_calls && result.tool_calls.length > 0 && (
            <div className="bg-elevated rounded-lg p-3 border border-blue-500/30">
              <div className="flex items-center gap-2 mb-2">
                <Wrench size={14} className="text-blue-400" />
                <span className="text-xs font-medium text-blue-400">Tool Invocations ({result.tool_calls.length})</span>
              </div>
              <div className="space-y-2">
                {result.tool_calls.map((tc, i) => (
                  <div key={i} className="bg-surface rounded-lg p-3 border border-blue-500/20">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm text-blue-300 font-mono font-bold">{tc.name}()</span>
                      <span className="text-[10px] text-muted">
                        {tc.duration_ms ? `${tc.duration_ms}ms` : ''} · {tc.type}
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      <div>
                        <div className="text-[9px] text-muted uppercase tracking-wide mb-0.5">Arguments</div>
                        <pre className="text-[11px] text-gray-200 bg-[var(--color-code-bg)] p-2 rounded font-mono overflow-x-auto border border-border/50">{
                          (() => { try { return JSON.stringify(JSON.parse(tc.arguments), null, 2) } catch { return tc.arguments } })()
                        }</pre>
                      </div>
                      {tc.result && (
                        <div>
                          <div className="text-[9px] text-muted uppercase tracking-wide mb-0.5">Result {tc._synthesized && <span className="text-yellow-400">(simulated)</span>}</div>
                          <pre className="text-[11px] text-green-300 bg-[var(--color-code-bg)] p-2 rounded font-mono overflow-x-auto border border-green-500/20">{tc.result}</pre>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Rich guardrail outcome (when guardrails fired) */}
          {exp?.guardrails && exp.guardrails.matches.length > 0 && (
            <div className="bg-elevated rounded-lg p-3 border border-red-500/30">
              <div className="flex items-center gap-2 mb-2">
                <Shield size={14} className="text-red-400" />
                <span className="text-xs font-medium text-red-400">Guardrail {exp.guardrails.decision === 'blocked' ? 'BLOCKED' : 'TRIGGERED'} ({exp.guardrails.matches.length} match{exp.guardrails.matches.length > 1 ? 'es' : ''})</span>
              </div>
              <div className="space-y-2">
                {exp.guardrails.matches.map((m, i) => (
                  <div key={i} className="bg-red-500/10 rounded p-2 border border-red-500/20">
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                      <KV k="Category" v={m.category} accent="red" />
                      <KV k="Detector" v={m.detector} />
                      <KV k="Rule" v={m.rule} />
                      <KV k="Action" v={m.action.toUpperCase()} accent="red" />
                      <KV k="Stage" v={m.stage} />
                      <KV k="Confidence" v={`${(m.confidence * 100).toFixed(0)}%`} />
                      <div className="col-span-2 mt-1">
                        <div className="text-[9px] text-muted uppercase mb-0.5">Matched value (masked)</div>
                        <code className="text-[11px] text-red-300 bg-[var(--color-code-bg)] px-2 py-1 rounded block font-mono">"{m.excerpt}"</code>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* PROXY ROUTING PATH - the request's journey, always visible */}
      <div className="rounded-lg px-3 py-2.5 border flex items-center gap-2 flex-wrap"
           style={{ background: 'var(--color-surface)', borderColor: 'var(--color-border)' }}
           data-testid="pg-routing-path">
        <span className="text-[9px] uppercase tracking-wider text-muted mr-1">Proxy routing path</span>
        {[
          { icon: <UserIcon size={11} />, label: 'Client' },
          { icon: <Boxes size={11} />, label: 'Gateway' },
          { icon: <Shield size={11} />, label: 'Policy Engine' },
          { icon: <Layers size={11} />, label: 'Router' },
          { icon: <Zap size={11} />, label: 'Provider' },
        ].map((n, i, arr) => (
          <span key={n.label} className="inline-flex items-center gap-2">
            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md"
                  style={{ background: 'var(--color-app)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}>
              <span className="text-accent">{n.icon}</span>{n.label}
            </span>
            {i < arr.length - 1 && <ArrowDown size={11} className="text-muted rotate-[-90deg]" />}
          </span>
        ))}
      </div>
    </div>
  )
}

// ─── Stage detail components ───
function StageDetail({ stageId, exp, result }: { stageId: string; exp: Explainability | null | undefined; result: RunResult | null }) {
  if (!exp) return <div className="text-[10px] text-muted py-2">No detail available.</div>

  if (stageId === 'auth') {
    return (
      <div className="text-[11px] text-gray-300 space-y-0.5 py-2">
        <KV k="Workspace" v={exp.auth.workspace_id} />
        <KV k="Client" v={exp.auth.client_id || '(none)'} />
        <KV k="User (JWT sub)" v={exp.auth.user_id} />
        <KV k="Method" v={exp.auth.auth_method} />
        <div className="text-[10px] text-muted mt-1">Behind the scenes: SHA-256(plaintext_key) → DB lookup → workspace context cached.</div>
      </div>
    )
  }

  if (stageId === 'routing') {
    const r = exp.routing
    return (
      <div className="text-[11px] text-gray-300 space-y-1 py-2">
        <KV k="Alias" v={r.alias} />
        <KV k="Reason" v={r.reason} />
        <div className="mt-1.5">
          <div className="text-[10px] text-muted mb-0.5">Candidates ({r.candidates.length}):</div>
          {r.candidates.map((c, i) => (
            <div key={i} className={`flex items-center gap-2 px-2 py-1 rounded text-[10px] mb-0.5 ${
              c.is_selected ? 'bg-accent/15 border border-accent/30' : 'bg-surface'
            }`}>
              <span className="font-mono text-[9px] text-muted w-14">{c.is_primary ? 'PRIMARY' : 'FALLBACK'}</span>
              <span className="font-mono">{c.provider}/{c.model_id}</span>
              <span className="text-[9px] text-muted">w={c.weight}</span>
              {c.is_selected && <span className="ml-auto text-accent text-[9px]">✓ SELECTED</span>}
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (stageId === 'guardrails') {
    const g = exp.guardrails
    return (
      <div className="text-[11px] text-gray-300 space-y-1 py-2">
        <KV k="Mode" v={g.mode} />
        <KV k="Decision" v={g.decision.toUpperCase()} accent={g.decision === 'blocked' ? 'red' : 'green'} />
        {g.matches && g.matches.length > 0 && (
          <div className="mt-1">
            <div className="text-[10px] text-muted mb-0.5">Triggered (what actually fired):</div>
            {g.matches.map((m, i) => (
              <div key={i} className="text-[10px] text-red-300">
                <span className="font-mono">{m.detector}</span>
                <span className="text-muted"> → {m.category}</span>
                {m.action ? <span className="text-muted"> · {m.action}</span> : null}
              </div>
            ))}
          </div>
        )}
        <div className="mt-1">
          <div className="text-[10px] text-muted mb-0.5">Enabled detectors (configured):</div>
          {g.enabled_detectors.length === 0 ? (
            <div className="text-[10px] text-muted italic">none</div>
          ) : g.enabled_detectors.map((d, i) => (
            <div key={i} className="text-[10px] text-gray-400">
              <span className="text-blue-300">{d.detector}</span>
              <span className="text-muted"> ← {d.matches.slice(0,4).join(', ')}{d.matches.length > 4 ? '...' : ''}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (stageId === 'rate_limit') {
    const rl = exp.rate_limit
    return (
      <div className="text-[11px] text-gray-300 space-y-1 py-2">
        <KV k="Window" v={rl.window} />
        <KV k="Decision" v={rl.overall_decision} accent={rl.overall_decision === 'BLOCK' ? 'red' : 'green'} />
        {rl.levels.map((l, i) => (
          <div key={i} className="flex items-center gap-2 text-[10px] py-0.5">
            <span className="font-mono w-32 text-muted">{l.level}</span>
            <span className="font-mono">{l.current} / {l.limit}</span>
            <div className="flex-1 h-1.5 bg-surface rounded overflow-hidden">
              <div className={`h-full transition-all ${l.current/l.limit > 0.8 ? 'bg-orange-400' : 'bg-green-400'}`}
                style={{ width: `${Math.min(100, (l.current/l.limit)*100)}%` }} />
            </div>
            <span className={`text-[9px] ${l.decision === 'ALLOW' ? 'text-green-400' : 'text-red-400'}`}>{l.decision}</span>
          </div>
        ))}
      </div>
    )
  }

  if (stageId === 'budget') {
    const b = exp.budget
    return (
      <div className="text-[11px] text-gray-300 space-y-1 py-2">
        <KV k="Decision" v={b.overall_decision} accent={b.overall_decision === 'BLOCK' ? 'red' : 'green'} />
        {b.levels.map((l, i) => (
          <div key={i} className="text-[10px] mb-1">
            <div className="flex items-center gap-2">
              <span className="font-mono w-20 text-muted">{l.level}</span>
              <span className="font-mono">${l.used_usd.toFixed(4)}</span>
              <span className="text-muted">of</span>
              <span className="font-mono">${l.limit_usd.toFixed(2)}</span>
              <span className="text-muted">({l.pct_used}%)</span>
              <span className={`ml-auto text-[9px] ${l.decision === 'ALLOW' ? 'text-green-400' : 'text-red-400'}`}>{l.decision}</span>
            </div>
            <div className="h-1.5 bg-surface rounded overflow-hidden mt-0.5">
              <div className={`h-full transition-all ${l.pct_used > 90 ? 'bg-red-400' : l.pct_used > 75 ? 'bg-orange-400' : 'bg-green-400'}`}
                style={{ width: `${Math.min(100, l.pct_used)}%` }} />
            </div>
          </div>
        ))}
      </div>
    )
  }

  if (stageId === 'engine') {
    return (
      <div className="text-[11px] text-gray-300 space-y-0.5 py-2">
        <KV k="Engine" v={result?.engine_used || '?'} />
        <KV k="Adapter contract" v="OpenAI HTTP wire (chat / embeddings / models)" />
        <KV k="Boundary rule" v="payloads = pure OpenAI · creds = side-channel" />
        <div className="text-[10px] text-muted mt-1">Behind the scenes: workspace creds Fernet-decrypted under master key, passed via x-bf-api-key header (Bifrost) or directly to provider SDK (Direct). Never traversed our governance pipeline.</div>
      </div>
    )
  }

  if (stageId === 'provider') {
    const ge = result?.governance_event
    if (!ge) return <div className="text-[10px] text-muted py-2">Provider was not invoked.</div>
    return (
      <div className="text-[11px] text-gray-300 space-y-0.5 py-2">
        <KV k="Provider" v={ge.provider} />
        <KV k="Model ID" v={ge.model_id} />
        <KV k="Input tokens" v={String(ge.input_tokens)} />
        <KV k="Output tokens" v={String(ge.output_tokens)} />
        <KV k="Provider latency" v={`${ge.latency_ms?.toFixed(0) || '?'}ms`} />
        <KV k="Cost (USD)" v={`$${ge.cost_usd.toFixed(6)}`} accent="green" />
      </div>
    )
  }

  if (stageId === 'governance') {
    return (
      <div className="text-[11px] text-gray-300 space-y-0.5 py-2">
        <div className="text-[10px] text-muted mb-1">Event emitted to all observers:</div>
        <KV k="Postgres" v="✓ persisted to request_logs" accent="green" />
        <KV k="SSE" v="✓ broadcast to live dashboard" accent="green" />
        <KV k="Kafka" v="✓ agnos-proxy.governance.v1" accent="green" />
        <KV k="Prometheus" v="✓ counters incremented" accent="green" />
        <KV k="OpenTelemetry" v="✓ spans → Jaeger" accent="green" />
      </div>
    )
  }
  return null
}

// ─── Small components ───
function IdField({ icon, label, value, readOnly, hint }: { icon: React.ReactNode; label: string; value: React.ReactNode; readOnly?: boolean; hint?: string }) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] text-muted mb-0.5">
        {icon}<span>{label}</span>
        {readOnly
          ? <span className="ml-auto inline-flex items-center gap-0.5 text-[8.5px] uppercase tracking-wide text-muted" title={hint}><Lock size={8} /> fixed</span>
          : <span className="ml-auto text-[8.5px] uppercase tracking-wide text-accent">editable</span>}
      </div>
      {value}
    </div>
  )
}
function KV({ k, v, accent }: { k: string; v: string; accent?: 'green' | 'red' }) {
  return (
    <div className="flex gap-2 text-[10px]">
      <span className="text-muted w-32 flex-shrink-0">{k}</span>
      <span className={`font-mono ${accent === 'green' ? 'text-green-400' : accent === 'red' ? 'text-red-400' : 'text-white'}`}>{v}</span>
    </div>
  )
}
function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div>
      <div className={`text-sm font-bold ${accent ? 'text-accent' : 'text-white'} font-mono`}>{value}</div>
      <div className="text-[10px] text-muted">{label}</div>
      {sub && <div className="text-[9px] text-muted/70">{sub}</div>}
    </div>
  )
}
