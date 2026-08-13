// Docs - a concise, copy-paste integration guide for internal component teams.
// Goal: a developer integrates the gateway in ~5 minutes. No backend calls;
// everything here is static reference + live-host base_url.
import { useState } from 'react'
import { Check, Copy, Terminal, KeyRound, Boxes, ShieldCheck, Activity, AlertTriangle } from 'lucide-react'
import { Card, SectionTitle } from '../components/ui'

const FRAMEWORKS = ['OpenAI SDK', 'LangChain', 'LangGraph', 'CrewAI', 'curl'] as const
type FW = typeof FRAMEWORKS[number]

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1200) }
  // Highlight the lines that actually matter for adopting the gateway - the
  // base_url (the only change), the workspace key, and the identity headers -
  // so the eye lands on them across every framework example.
  const lineClass = (line: string): string => {
    const l = line.toLowerCase()
    if (l.includes('base_url') || l.includes('openai_api_base') || l.includes('api_base'))
      return 'bg-accent/15 text-accent -mx-4 px-4 border-l-2 border-accent'
    if (l.includes('api_key') || l.includes('openai_api_key') || l.includes('bearer'))
      return 'bg-amber-500/10 text-amber-300 -mx-4 px-4 border-l-2 border-amber-400'
    if (l.includes('x-gateway') || l.includes('default_headers') || l.includes('extra_headers')
        || l.includes('metadata') || l.includes('use_case') || l.includes('openai_default_headers'))
      return 'bg-violet-500/10 text-violet-300 -mx-4 px-4 border-l-2 border-violet-400'
    return ''
  }
  return (
    <div className="relative rounded-lg overflow-hidden border" style={{ borderColor: 'var(--color-border)' }}>
      <button onClick={copy} className="absolute top-2 right-2 z-10 text-[10px] flex items-center gap-1 px-2 py-1 rounded bg-elevated/80 hover:bg-elevated text-muted hover:text-white">
        {copied ? <><Check size={11} /> Copied</> : <><Copy size={11} /> Copy</>}
      </button>
      <pre className="text-[12px] leading-relaxed p-4 overflow-x-auto" style={{ background: 'var(--color-code-bg)', color: 'var(--color-code-text)' }}>
        {code.split('\n').map((line, i) => (
          <div key={i} className={lineClass(line)}>{line || '\u00A0'}</div>
        ))}
      </pre>
      <div className="px-4 py-1.5 border-t text-[9px] text-muted flex flex-wrap gap-x-3" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}>
        <span><span className="inline-block w-2 h-0.5 bg-accent align-middle mr-1" />base_url (the only change)</span>
        <span><span className="inline-block w-2 h-0.5 bg-amber-400 align-middle mr-1" />workspace key</span>
        <span><span className="inline-block w-2 h-0.5 bg-violet-400 align-middle mr-1" />identity headers</span>
      </div>
    </div>
  )
}

// A compact, never-overflowing value field with a one-click copy button.
// Middle-truncates long values (preserving the recognizable head + tail) so a
// long gateway URL or key prefix can't bleed across the card margins.
function CopyBadge({ value, mono = true }: { value: string; mono?: boolean }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1200) }
  return (
    <button
      onClick={copy}
      title={`Copy: ${value}`}
      className="group/cb mt-1.5 w-full flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left hover:border-accent/60 transition-colors"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface)' }}
    >
      <span className={`flex-1 min-w-0 truncate text-[11.5px] ${mono ? 'mono' : ''}`} style={{ color: 'var(--color-text)' }}>{value}</span>
      <span className="shrink-0 text-[10px] flex items-center gap-1 text-muted group-hover/cb:text-accent">
        {copied ? <><Check size={11} /> Copied</> : <><Copy size={11} /> Copy</>}
      </span>
    </button>
  )
}

export function Docs() {
  const base = typeof window !== 'undefined' ? window.location.origin : 'https://gateway.example'
  const [fw, setFw] = useState<FW>('OpenAI SDK')
  const [tab, setTab] = useState<'quickstart' | 'headers' | 'errors'>('quickstart')

  const snippets: Record<FW, string> = {
    'OpenAI SDK': `from openai import OpenAI

client = OpenAI(
    base_url="${base}/v1",          # the ONLY change vs. calling OpenAI directly
    api_key="gw-your-workspace-key", # a workspace key, never a provider key
)

resp = client.chat.completions.create(
    model="default",                 # → your workspace's default model (or pass an alias)
    messages=[{"role": "user", "content": "Hello"}],
    metadata={"use_case": "my-feature"},     # attribution (also accepts header below)
    extra_headers={
        "X-Gateway-Component": "my-service",
        "X-Gateway-User": "user-123",
    },
)
print(resp.choices[0].message.content)`,
    LangChain: `from langchain.chat_models import init_chat_model

llm = init_chat_model(
    "default",                       # → workspace default model
    model_provider="openai",
    base_url="${base}/v1",
    api_key="gw-your-workspace-key",
    default_headers={
        "X-Gateway-Component": "my-service",
        "X-Gateway-User": "user-123",
        "X-Gateway-Use-Case": "my-feature",
    },
)
print(llm.invoke("Hello").content)`,
    LangGraph: `from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, MessagesState, START, END

llm = init_chat_model(
    "default", model_provider="openai",
    base_url="${base}/v1", api_key="gw-your-workspace-key",
    default_headers={
        "X-Gateway-Component": "my-agent",
        "X-Gateway-User": "user-123",
        "X-Gateway-Use-Case": "my-feature",
    },
)

def agent(state: MessagesState):
    return {"messages": [llm.invoke(state["messages"])]}

g = StateGraph(MessagesState); g.add_node("agent", agent)
g.add_edge(START, "agent"); g.add_edge("agent", END)
app = g.compile()`,
    CrewAI: `import os
os.environ["OPENAI_API_BASE"]  = "${base}/v1"
os.environ["OPENAI_API_KEY"]   = "gw-your-workspace-key"
os.environ["OPENAI_MODEL_NAME"] = "default"
os.environ["OPENAI_DEFAULT_HEADERS"] = (
    '{"X-Gateway-Component": "my-crew", '
    '"X-Gateway-User": "user-123", '
    '"X-Gateway-Use-Case": "my-feature"}'
)
# ...build your Crew as usual`,
    curl: `curl ${base}/v1/chat/completions \\
  -H "Authorization: Bearer gw-your-workspace-key" \\
  -H "Content-Type: application/json" \\
  -H "X-Gateway-Component: my-service" \\
  -H "X-Gateway-User: user-123" \\
  -H "X-Gateway-Use-Case: my-feature" \\
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
  }

  return (
    <div className="space-y-5 max-w-4xl">
      <div>
        <h1 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>Integrate in 5 minutes</h1>
        <p className="text-muted text-sm">
          The gateway is an OpenAI-compatible endpoint. Point your existing client at it with three changes - then you
          inherit routing, guardrails, budgets, cost attribution and full observability with zero extra code.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b" style={{ borderColor: 'var(--color-border)' }}>
        {([
          ['quickstart', '🚀 Quick Start'],
          ['headers', '🔤 Header Reference'],
          ['errors', '⚠️ Error Codes'],
        ] as const).map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            data-testid={`docs-tab-${id}`}
            className="px-3 py-2 text-[13px] -mb-px border-b-2 transition-colors"
            style={tab === id
              ? { borderColor: 'var(--color-accent)', color: 'var(--color-text)', fontWeight: 600 }
              : { borderColor: 'transparent', color: 'var(--color-muted)' }}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'quickstart' && <>
      {/* 3-step quickstart */}
      <Card>
        <SectionTitle>The only 3 changes</SectionTitle>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {[
            { icon: <Terminal size={16} />, t: '1 · base_url', d: <>Set this as your client's base URL.</>, copy: `${base}/v1` },
            { icon: <KeyRound size={16} />, t: '2 · workspace key', d: <>Authenticate with a <code className="text-accent">gw-…</code> workspace key (issued under API Keys). Never a provider key.</>, copy: 'gw-your-workspace-key' },
            { icon: <Boxes size={16} />, t: '3 · model', d: <>Use <code className="text-accent">"default"</code> (your workspace default) or an explicit alias. You never hold provider creds.</> },
          ].map((s) => (
            <div key={s.t} className="rounded-lg p-3 border min-w-0 overflow-hidden" style={{ borderColor: 'var(--color-border)', background: 'var(--color-app)' }}>
              <div className="flex items-center gap-2 text-accent mb-1">{s.icon}<span className="text-[13px] font-semibold" style={{ color: 'var(--color-text)' }}>{s.t}</span></div>
              <div className="text-[12px] break-words" style={{ color: 'var(--color-text-secondary)' }}>{s.d}</div>
              {s.copy && <CopyBadge value={s.copy} />}
            </div>
          ))}
        </div>
      </Card>

      {/* Code */}
      <Card>
        <SectionTitle right={
          <div className="flex bg-elevated rounded-xl p-0.5">
            {FRAMEWORKS.map((f) => (
              <button key={f} onClick={() => setFw(f)}
                className={`px-2.5 py-1 rounded-lg text-xs ${fw === f ? 'bg-accent text-white' : 'text-muted'}`}>{f}</button>
            ))}
          </div>
        }>Copy-paste example</SectionTitle>
        <CodeBlock code={snippets[fw]} />
      </Card>
      </>}

      {tab === 'headers' && <>
      {/* Identity & attribution */}
      <Card>
        <SectionTitle>Identity & attribution headers</SectionTitle>
        <div className="text-[12px] space-y-2" style={{ color: 'var(--color-text-secondary)' }}>
          <p>Every request should carry who/what is calling so cost and policy attribute correctly:</p>
          <table className="w-full text-[12px]">
            <thead><tr className="text-left text-muted">
              <th className="py-1">Field</th><th>How to send</th><th>Purpose</th>
            </tr></thead>
            <tbody>
              <tr className="border-t" style={{ borderColor: 'var(--color-border)' }}>
                <td className="py-1.5 mono text-accent">X-Gateway-Component</td><td>header</td><td>Which service/agent is calling.</td>
              </tr>
              <tr className="border-t" style={{ borderColor: 'var(--color-border)' }}>
                <td className="py-1.5 mono text-accent">X-Gateway-User</td><td>header</td><td>End-user (or service principal) for per-user budgets.</td>
              </tr>
              <tr className="border-t" style={{ borderColor: 'var(--color-border)' }}>
                <td className="py-1.5 mono text-accent">use_case</td><td><code>metadata.use_case</code> in the body, or <code className="mono">X-Gateway-Use-Case</code> header</td><td>Logical workflow label for analytics & latency views.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>
      </>}

      {tab === 'errors' && <>
      {/* What you get */}
      <Card>
        <SectionTitle>What you get for free</SectionTitle>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
          {[
            [<ShieldCheck size={13} key="g" />, 'Guardrails - PII/secrets detection, CEL rules, block/redact/audit, all at the boundary.'],
            [<Activity size={13} key="o" />, 'Observability - every call is one attributed event: cost, tokens, latency, traces.'],
            [<Boxes size={13} key="r" />, 'Routing & fallback - alias → provider with weighted load-balancing and failover.'],
            [<KeyRound size={13} key="b" />, 'Budgets & rate-limits - hierarchical caps (client → workspace → user).'],
          ].map(([ic, t], i) => (
            <div key={i} className="flex items-start gap-2 rounded-lg p-2.5 border" style={{ borderColor: 'var(--color-border)' }}>
              <span className="text-accent mt-0.5">{ic}</span><span>{t}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Error reference */}
      <Card>
        <SectionTitle>Error reference</SectionTitle>
        <div className="text-[12px]">
          <table className="w-full">
            <thead><tr className="text-left text-muted"><th className="py-1">HTTP</th><th>type</th><th>What it means / fix</th></tr></thead>
            <tbody style={{ color: 'var(--color-text-secondary)' }}>
              {[
                ['401', 'authentication_error', 'Bad/disabled workspace key.'],
                ['404', 'invalid_request_error', 'Model alias not registered for the workspace. Use "default" or a registered alias.'],
                ['422', 'guardrail_violation', 'Blocked by a guardrail rule (e.g. PII/secret). Check the message for the rule.'],
                ['429', 'rate_limit', 'RPM/TPM exceeded for user/workspace/client. Back off and retry.'],
                ['402', 'budget_exceeded', 'Spend cap reached for the breached scope. Raise the budget or wait for the window.'],
              ].map(([c, t, d]) => (
                <tr key={c} className="border-t" style={{ borderColor: 'var(--color-border)' }}>
                  <td className="py-1.5 mono" style={{ color: 'var(--color-text)' }}>{c}</td>
                  <td className="mono text-accent">{t}</td>
                  <td className="flex items-start gap-1"><AlertTriangle size={11} className="text-warn mt-0.5 shrink-0" />{d}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      </>}
    </div>
  )
}
