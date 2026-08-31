// Guardrails → Providers page.
// Card catalog of detector types + per-provider config forms.
// Implemented: Custom Regex (native), Secrets Detection (native, gitleaks),
// AWS Bedrock Guardrails (live). Scaffolded: Azure Content Safety. Not yet implemented:
// Google Model Armor, Patronus, CrowdStrike, GraySwan.

import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  AlertCircle, Check, ChevronLeft, FlaskConical, Loader2, Plus, ShieldCheck,
  Sparkles, Trash2, X,
} from 'lucide-react'
import { admin } from '../api/client'
import type { DetectorType } from '../api/types'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Pill, SectionTitle, Skeleton } from '../components/ui'

interface Profile {
  id: number; name: string; detector_type: string; enabled: boolean; config: any;
  policy_name?: string; scope?: string; workspace_id?: string | null;
}

interface CatalogItem {
  type: DetectorType
  label: string
  blurb: string
  status: 'native' | 'live' | 'scaffold'
  iconHue: string
}

const CATALOG: CatalogItem[] = [
  { type: 'regex', label: 'Custom Regex', blurb: 'Author your own pattern set or use the built-in PII template (email, phone, SSN, credit card, IPv4).',
    status: 'native', iconHue: '#6366F1' },
  { type: 'secrets', label: 'Secrets Detection', blurb: 'Gitleaks-style high-signal secret patterns (AWS keys, OpenAI/Anthropic keys, RSA private keys).',
    status: 'native', iconHue: '#F87171' },
  { type: 'keyword', label: 'Keyword Blocklist', blurb: 'Plain-text blocklist scanned across messages (case-insensitive).',
    status: 'native', iconHue: '#FBBF24' },
  { type: 'presidio', label: 'Microsoft Presidio', blurb: 'Open-source PII analyzer (PERSON, LOCATION, NRP, …) running in our process.',
    status: 'native', iconHue: '#A78BFA' },
  { type: 'bedrock', label: 'AWS Bedrock Guardrails', blurb: 'Apply your AWS Bedrock guardrail (content / topic / word / contextual-grounding policy) as a profile.',
    status: 'live', iconHue: '#FB923C' },
  { type: 'azure', label: 'Azure Content Safety', blurb: 'Severity-thresholded category filters (hate, sexual, self-harm, violence) and Prompt Shields.',
    status: 'scaffold', iconHue: '#22D3EE' },
]

const STATUS_BADGES: Record<CatalogItem['status'], { label: string; color: string }> = {
  'native': { label: 'NATIVE', color: '#6366F1' },
  'live': { label: 'LIVE', color: '#34D399' },
  'scaffold': { label: 'SCAFFOLD', color: '#FBBF24' },
}

export function GuardrailsProviders() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<CatalogItem | null>(null)

  const load = async () => {
    setLoading(true)
    try { const r = await admin.listProfiles(); setProfiles(r.profiles || []) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])

  if (selected) {
    return (
      <ProviderDetail item={selected} profiles={profiles.filter((p) => p.detector_type === selected.type)}
                      onBack={() => setSelected(null)} reload={load} />
    )
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Guardrails · Providers</h1>
        <p className="text-muted text-sm">A reusable detector profile is a configured detector - patterns, policies, or hosted guardrail. Profiles plug into rules.</p>
      </div>

      {loading ? <Skeleton h={300} /> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="provider-catalog">
          {CATALOG.map((c) => {
            const count = profiles.filter((p) => p.detector_type === c.type).length
            return (
              <button key={c.type}
                data-testid={`provider-card-${c.type}`}
                onClick={() => setSelected(c)}
                className="card p-5 text-left transition-colors hover:border-accent/50 cursor-pointer">
                <div className="flex items-center justify-between mb-2">
                  <div className="w-9 h-9 rounded-xl flex items-center justify-center"
                       style={{ background: c.iconHue + '22', color: c.iconHue }}>
                    <ShieldCheck size={18} />
                  </div>
                  <Pill color={STATUS_BADGES[c.status].color}>{STATUS_BADGES[c.status].label}</Pill>
                </div>
                <div className="text-base font-semibold text-white mb-1">{c.label}</div>
                <div className="text-[12px] text-muted leading-relaxed mb-3">{c.blurb}</div>
                <div className="text-[11px] text-muted flex items-center justify-between">
                  <span>{`${count} configuration${count === 1 ? '' : 's'}`}</span>
                  <span className="text-accent">configure →</span>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─────────────────────── Provider detail ───────────────────────

function ProviderDetail({ item, profiles, onBack, reload }:
  { item: CatalogItem; profiles: Profile[]; onBack: () => void; reload: () => void }) {
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Profile | null>(null)
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <button onClick={onBack} className="text-[11px] text-muted hover:text-gray-200 mb-2 flex w-fit items-center gap-1">
            <ChevronLeft size={13} /> Back to providers
          </button>
          <h1 className="text-xl font-semibold flex items-center gap-3">
            {item.label} <Pill color={STATUS_BADGES[item.status].color}>{STATUS_BADGES[item.status].label}</Pill>
          </h1>
          <p className="text-muted text-sm max-w-2xl mt-1">{item.blurb}</p>
        </div>
        <button data-testid="profile-new" className="btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} /> Add configuration
        </button>
      </div>

      <Card className="p-0">
        {profiles.length === 0 ? (
          <div className="p-10">
            <EmptyState icon={<Sparkles size={32} />}
              title={`No ${item.label} configurations yet`}
              hint="Create one. Each configuration is a reusable detector profile you can link from any rule."
              cta={<button className="btn-primary" onClick={() => setCreating(true)}><Plus size={14} /> Create configuration</button>} />
          </div>
        ) : (
          <div className="overflow-x-auto" data-testid="profile-list">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">ID</th>
                  <th className="th">Name</th>
                  <th className="th">Enabled</th>
                  <th className="th">Summary</th>
                  <th className="th"></th>
                </tr>
              </thead>
              <tbody>
                {profiles.map((p) => (
                  <tr key={p.id} data-testid={`profile-row-${p.id}`}
                      className="border-t border-border/60 hover:bg-elevated/40 cursor-pointer"
                      onClick={() => setEditing(p)}>
                    <td className="td mono text-[11px] text-muted">#{p.id}</td>
                    <td className="td text-gray-100 font-medium">{p.name}</td>
                    <td className="td">{p.enabled
                      ? <span className="text-ok inline-flex items-center gap-1 text-xs"><Check size={13} /> active</span>
                      : <span className="text-muted text-xs">disabled</span>}</td>
                    <td className="td text-muted text-xs mono truncate max-w-[420px]">
                      {summarize(item.type as DetectorType, p.config)}
                    </td>
                    <td className="td text-right" onClick={(e) => e.stopPropagation()}>
                      <button data-testid={`profile-delete-${p.id}`} className="text-danger"
                        onClick={async () => {
                          await withToast(() => admin.deleteProfile(p.id), 'Profile deleted'); reload()
                        }}><Trash2 size={14} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <AnimatePresence>
        {(creating || editing) && (
          <ConfigEditor item={item} profile={editing} onClose={() => { setCreating(false); setEditing(null) }}
            onSaved={() => { setCreating(false); setEditing(null); reload() }} />
        )}
      </AnimatePresence>
    </div>
  )
}

function summarize(type: DetectorType, cfg: any): string {
  cfg = cfg || {}
  if (type === 'regex') return `${(cfg.patterns ? Object.keys(cfg.patterns).length : 0)} pattern(s) · action ${cfg.action || 'block'}`
  if (type === 'secrets') return `allowlist: ${(cfg.allow || []).length} term(s)`
  if (type === 'keyword') return `${(cfg.keywords || []).length} keyword(s)`
  if (type === 'presidio') return `entities: ${(cfg.entities || ['default']).join(', ')}`
  if (type === 'bedrock') return `guardrail: ${cfg.guardrail_id || cfg.guardrail_arn || '-'} · ${cfg.region || 'us-east-1'}`
  if (type === 'azure') return `endpoint: ${cfg.endpoint || '-'}`
  return JSON.stringify(cfg).slice(0, 80)
}

// ─────────────────────── Config editor (per-provider form) ───────────────────────

function ConfigEditor({ item, profile, onClose, onSaved }:
  { item: CatalogItem; profile: Profile | null; onClose: () => void; onSaved: () => void }) {
  const isEdit = !!profile
  const [name, setName] = useState(profile?.name ?? '')
  const [enabled, setEnabled] = useState(profile?.enabled ?? true)
  const [config, setConfig] = useState<any>(profile?.config ?? defaultConfig(item.type as DetectorType))
  const [busy, setBusy] = useState(false)

  async function save() {
    if (!name.trim()) { toastError('Name is required'); return }
    setBusy(true)
    try {
      if (isEdit) await admin.updateProfile(profile!.id, { name, enabled, config })
      else await admin.createProfile({ name, detector_type: item.type as any, enabled, config })
      toastOk(isEdit ? 'Configuration updated' : 'Configuration created')
      onSaved()
    } catch (e: any) { toastError(e.message || 'save failed') } finally { setBusy(false) }
  }

  return (
    <>
      <motion.div className="fixed inset-0 bg-black/60 z-40" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <motion.div data-testid="profile-editor"
          className="w-[720px] max-w-[96vw] max-h-[90vh] card p-0 flex flex-col overflow-hidden pointer-events-auto"
          initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.96 }}>
          <div className="px-6 pt-5 pb-3 border-b border-border flex items-center justify-between">
            <div>
              <div className="text-base font-semibold text-white">{isEdit ? 'Edit' : 'New'} {item.label} configuration</div>
              <div className="text-[11px] text-muted">Reusable detector profile · linked from rules.</div>
            </div>
            <button onClick={onClose} className="text-muted hover:text-gray-200"><X size={18} /></button>
          </div>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5 space-y-5">
            <div className="grid grid-cols-[1fr_auto] gap-4 items-end">
              <div>
                <div className="text-sm text-gray-200 font-medium mb-1.5">Name</div>
                <input data-testid="profile-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
                <div className="text-[11px] text-muted mt-1">Shown in the rules editor when linking profiles.</div>
              </div>
              <label className="text-[11px] text-muted inline-flex items-center gap-2">
                <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
              </label>
            </div>

            {item.type === 'regex' && <RegexConfig cfg={config} setCfg={setConfig} />}
            {item.type === 'secrets' && <SecretsConfig cfg={config} setCfg={setConfig} />}
            {item.type === 'keyword' && <KeywordConfig cfg={config} setCfg={setConfig} />}
            {item.type === 'presidio' && <PresidioConfig cfg={config} setCfg={setConfig} />}
            {item.type === 'bedrock' && <BedrockConfig cfg={config} setCfg={setConfig} />}
            {item.type === 'azure' && <AzureConfig cfg={config} setCfg={setConfig} />}
          </div>

          <div className="px-6 py-3 border-t border-border flex justify-between items-center bg-surface">
            <div className="text-[11px] text-muted">{isEdit ? `Editing #${profile!.id}` : 'New configuration'}</div>
            <div className="flex items-center gap-2">
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
              <button data-testid="profile-save" className="btn-primary" disabled={busy || !name.trim()} onClick={save}>
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {isEdit ? 'Save changes' : 'Save configuration'}
              </button>
            </div>
          </div>
        </motion.div>
      </div>
    </>
  )
}

function defaultConfig(type: DetectorType): any {
  if (type === 'regex') return { patterns: {}, action: 'block' }
  if (type === 'secrets') return { allow: [] }
  if (type === 'keyword') return { keywords: [] }
  if (type === 'presidio') return { entities: ['PERSON', 'EMAIL_ADDRESS', 'PHONE_NUMBER'] }
  if (type === 'bedrock') return { guardrail_id: '', guardrail_version: 'DRAFT', region: 'us-east-1' }
  if (type === 'azure') return { endpoint: '', api_key: '', severity: 'medium', shields: { jailbreak: true, indirect: true }, blocklists: [] }
  return {}
}

// ─────── per-provider sub-forms ───────

const PII_TEMPLATE = {
  email: '\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b',
  phone: '\\b\\+?1?[\\s.-]?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4}\\b',
  ssn: '\\b\\d{3}-\\d{2}-\\d{4}\\b',
  credit_card: '\\b(?:\\d{4}[\\s-]?){3}\\d{4}\\b',
  ipv4: '\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b',
}

function RegexConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const patterns: Record<string, string> = cfg.patterns || {}
  const rows = Object.entries(patterns)
  function setPattern(k: string, p: string) { setCfg({ ...cfg, patterns: { ...patterns, [k]: p } }) }
  function setKey(oldK: string, newK: string) {
    if (newK === oldK) return
    const next: any = {}
    for (const [k, v] of Object.entries(patterns)) next[k === oldK ? newK : k] = v
    setCfg({ ...cfg, patterns: next })
  }
  function deleteRow(k: string) { const next = { ...patterns }; delete next[k]; setCfg({ ...cfg, patterns: next }) }
  function addRow() { setCfg({ ...cfg, patterns: { ...patterns, [`pattern_${rows.length + 1}`]: '' } }) }
  function applyPII() { setCfg({ ...cfg, patterns: { ...patterns, ...PII_TEMPLATE } }) }
  return (
    <div className="space-y-3" data-testid="regex-config">
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-200">Patterns</div>
        <div className="flex items-center gap-2">
          <button data-testid="regex-pii-template" className="btn-ghost text-xs" onClick={applyPII}>
            <Sparkles size={12} /> PII Detection template
          </button>
          <button data-testid="regex-add-row" className="btn-ghost text-xs" onClick={addRow}>
            <Plus size={12} /> Add pattern
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="text-[11px] text-muted py-4 italic">
          No patterns yet. Click <span className="text-gray-300">PII Detection template</span> for a quick start, or add your own.
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map(([k, v], i) => (
            <div key={i} className="grid grid-cols-[180px_1fr_auto] gap-2 items-center"
                 data-testid={`regex-row-${i}`}>
              <input className="input py-1.5 text-xs" placeholder="category" value={k}
                     onChange={(e) => setKey(k, e.target.value)} data-testid={`regex-key-${i}`} />
              <input className="input py-1.5 text-xs mono" placeholder="regex pattern" value={v}
                     onChange={(e) => setPattern(k, e.target.value)} data-testid={`regex-val-${i}`} />
              <button onClick={() => deleteRow(k)} className="text-danger"><X size={13} /></button>
            </div>
          ))}
        </div>
      )}
      <Hint>Patterns are matched against message text. Categories appear as <span className="mono text-gray-300">regex_pii:&lt;category&gt;</span> in violations.</Hint>
    </div>
  )
}

function SecretsConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const allow: string[] = cfg.allow || []
  const [draft, setDraft] = useState('')
  function add() { if (!draft.trim()) return; setCfg({ ...cfg, allow: [...allow, draft.trim()] }); setDraft('') }
  function remove(i: number) { setCfg({ ...cfg, allow: allow.filter((_, j) => j !== i) }) }
  return (
    <div className="space-y-2" data-testid="secrets-config">
      <div className="text-sm font-semibold text-gray-200">False-positive allowlist</div>
      <div className="bg-app border border-border rounded-xl p-2 flex flex-wrap items-center gap-1 min-h-[44px]">
        {allow.map((a, i) => (
          <span key={i} className="pill" style={{ background: '#A78BFA1f', color: '#A78BFA', borderColor: '#A78BFA55' }}>
            {a}<button onClick={() => remove(i)} className="ml-1"><X size={11} /></button>
          </span>
        ))}
        <input data-testid="secrets-draft" placeholder="add term + Enter" className="bg-transparent outline-none text-sm flex-1 min-w-[120px] px-1"
               value={draft} onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
      </div>
      <Hint>Substrings to ignore when the gitleaks ruleset would otherwise flag them (e.g., known fake/example keys).</Hint>
    </div>
  )
}

function KeywordConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const kws: string[] = cfg.keywords || []
  const [draft, setDraft] = useState('')
  function add() { if (!draft.trim()) return; setCfg({ ...cfg, keywords: [...kws, draft.trim()] }); setDraft('') }
  function remove(i: number) { setCfg({ ...cfg, keywords: kws.filter((_, j) => j !== i) }) }
  return (
    <div className="space-y-2" data-testid="keyword-config">
      <div className="text-sm font-semibold text-gray-200">Blocked keywords</div>
      <div className="bg-app border border-border rounded-xl p-2 flex flex-wrap items-center gap-1 min-h-[44px]">
        {kws.map((k, i) => (
          <span key={i} className="pill" style={{ background: '#FBBF241f', color: '#FBBF24', borderColor: '#FBBF2455' }}>
            {k}<button onClick={() => remove(i)} className="ml-1"><X size={11} /></button>
          </span>
        ))}
        <input data-testid="keyword-draft" placeholder="add keyword + Enter" className="bg-transparent outline-none text-sm flex-1 min-w-[120px] px-1"
               value={draft} onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
      </div>
      <Hint>Case-insensitive substring match across all message text.</Hint>
    </div>
  )
}

function PresidioConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const ents: string[] = cfg.entities || []
  return (
    <div className="space-y-2" data-testid="presidio-config">
      <div className="text-sm font-semibold text-gray-200">Entities to detect</div>
      <input className="input mono text-xs" value={ents.join(', ')}
        onChange={(e) => setCfg({ ...cfg, entities: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })}
        data-testid="presidio-entities" />
      <Hint>Comma-separated. Common values: PERSON, EMAIL_ADDRESS, PHONE_NUMBER, LOCATION, NRP, IP_ADDRESS.</Hint>
    </div>
  )
}

function BedrockConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const [testing, setTesting] = useState<'idle' | 'busy' | 'ok' | 'fail'>('idle')
  const [testMsg, setTestMsg] = useState('')
  const set = (k: string) => (v: any) => setCfg({ ...cfg, [k]: v })

  // Required for the Test button (mirrors REQUIRED_CREDS server-side)
  const ready = (cfg.access_key || '').trim() && (cfg.secret_key || '').trim() && (cfg.guardrail_id || '').trim()
  // STS / temporary credentials (access keys starting with "ASIA") REQUIRE a session token.
  const looksTemporary = (cfg.access_key || '').trim().toUpperCase().startsWith('ASIA')
  async function runTest() {
    setTesting('busy'); setTestMsg('')
    try {
      // Real connectivity check: calls ApplyGuardrail (exactly what the runtime
      // detector does) with only the typed creds - validates guardrail_id +
      // region + creds (incl. STS session token) together.
      const r = await admin.testGuardrailProfile({
        detector_type: 'bedrock',
        config: {
          access_key: cfg.access_key,
          secret_key: cfg.secret_key,
          session_token: cfg.session_token || undefined,
          guardrail_id: cfg.guardrail_id,
          guardrail_version: cfg.guardrail_version || 'DRAFT',
          region: cfg.region || 'us-east-1',
          timeout_ms: cfg.timeout_ms || 5000,
        },
      })
      setTesting(r.ok ? 'ok' : 'fail')
      setTestMsg(r.ok ? `Guardrail reachable · action=${r.detail?.action || 'NONE'} · ${r.latency_ms} ms` : (r.error || 'failed'))
    } catch (e: any) { setTesting('fail'); setTestMsg(e.message || 'failed') }
  }
  return (
    <div className="space-y-5" data-testid="bedrock-config">
      {/* ── AWS credentials ── */}
      <section className="space-y-3">
        <div className="text-[11px] uppercase tracking-wider font-semibold text-muted">AWS credentials</div>
        <div className="grid grid-cols-2 gap-3">
          <Sub label="Access key ID" hint="AWS access key id with bedrock:ApplyGuardrail permission.">
            <input data-testid="bedrock-access-key" className="input" type="password" autoComplete="off" value={cfg.access_key || ''} onChange={(e) => set('access_key')(e.target.value.trim())} />
          </Sub>
          <Sub label="Secret access key" hint="Stored encrypted; never logged.">
            <input data-testid="bedrock-secret-key" className="input" type="password" autoComplete="off" value={cfg.secret_key || ''} onChange={(e) => set('secret_key')(e.target.value.trim())} />
          </Sub>
        </div>
        {looksTemporary && !(cfg.session_token || '').trim() && (
          <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2.5 text-[12px] text-warn flex items-start gap-2 leading-relaxed">
            <AlertCircle size={14} className="shrink-0 mt-0.5" />
            <p>
              Using a temporary key starting with <span className="mono font-semibold">ASIA</span>? You must also fill in the
              {' '}<span className="font-semibold">Session token</span> field below, or AWS will reject it with
              {' '}<span className="mono">UnrecognizedClientException</span>.
            </p>
          </div>
        )}
        <Sub label={`Session token${looksTemporary ? '  (required)' : '  (optional)'}`}
             hint="Required when using temporary STS credentials (access key starts with ASIA…).">
          <input data-testid="bedrock-session-token" className="input font-mono text-[11px]" type="password" autoComplete="off"
                 placeholder="Paste the matching STS session token for ASIA… keys"
                 value={cfg.session_token || ''} onChange={(e) => set('session_token')(e.target.value.trim())} />
        </Sub>
      </section>

      {/* ── Guardrail target ── */}
      <section className="space-y-3">
        <div className="text-[11px] uppercase tracking-wider font-semibold text-muted">Guardrail</div>
        <Sub label="Guardrail ID / ARN" hint="From AWS Bedrock console → Guardrails. A pasted 'ARN: …' label is cleaned automatically.">
          <input data-testid="bedrock-guardrail-id" className="input mono text-xs" placeholder="arn:aws:bedrock:ap-south-1:…:guardrail/abc123"
                 value={cfg.guardrail_id || ''} onChange={(e) => set('guardrail_id')(e.target.value)} />
        </Sub>
        <div className="grid grid-cols-3 gap-3">
          <Sub label="Region" hint="Any AWS region the guardrail lives in (e.g. ap-south-1).">
            <input data-testid="bedrock-region" className="input mono text-xs" placeholder="ap-south-1"
                   value={cfg.region || ''} onChange={(e) => set('region')(e.target.value.trim())} />
          </Sub>
          <Sub label="Version" hint="DRAFT or a numeric version ('Working draft' is normalized to DRAFT).">
            <input data-testid="bedrock-version" className="input" placeholder="DRAFT" value={cfg.guardrail_version || ''} onChange={(e) => set('guardrail_version')(e.target.value)} />
          </Sub>
          <Sub label="Timeout (ms)" hint="Per-call upper bound; exceeded = fail-open (request continues).">
            <input data-testid="bedrock-timeout" className="input" type="number" value={cfg.timeout_ms || 5000} onChange={(e) => set('timeout_ms')(Number(e.target.value))} />
          </Sub>
        </div>
      </section>

      {/* ── Connectivity test ── */}
      <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-border/60">
        <button data-testid="bedrock-test" className="btn-ghost mt-3" disabled={!ready || testing === 'busy'} onClick={runTest}>
          {testing === 'busy' ? <Loader2 size={13} className="animate-spin" /> : <FlaskConical size={13} />} Test connection
        </button>
        {testing === 'ok' && <Pill color="#34D399"><Check size={11} /> {testMsg}</Pill>}
        {testing === 'fail' && <span data-testid="bedrock-test-fail" className="text-[11.5px] text-danger flex-1 min-w-[200px]">{testMsg}</span>}
      </div>
      <Hint>Calls the real <span className="mono">ApplyGuardrail</span> API using <span className="text-gray-300">only the credentials you typed</span> - no environment/stored AWS fallback.</Hint>
    </div>
  )
}

function AzureConfig({ cfg, setCfg }: { cfg: any; setCfg: (c: any) => void }) {
  const set = (k: string) => (v: any) => setCfg({ ...cfg, [k]: v })
  return (
    <div className="space-y-3" data-testid="azure-config">
      <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-[12px] text-warn flex items-start gap-2">
        <AlertCircle size={14} className="shrink-0 mt-0.5" />
        Azure Content Safety is scaffolded. Detector returns <span className="mono">NotConfigured</span> until creds + a tenant policy are supplied.
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Sub label="Endpoint" hint="https://&lt;name&gt;.cognitiveservices.azure.com/">
          <input className="input" value={cfg.endpoint || ''} onChange={(e) => set('endpoint')(e.target.value)} />
        </Sub>
        <Sub label="API key" hint="Stored encrypted.">
          <input className="input" type="password" value={cfg.api_key || ''} onChange={(e) => set('api_key')(e.target.value)} />
        </Sub>
        <Sub label="Severity threshold" hint="Block when Azure severity ≥ this level.">
          <select className="input" value={cfg.severity || 'medium'} onChange={(e) => set('severity')(e.target.value)}>
            {['low', 'medium', 'high'].map((s) => <option key={s}>{s}</option>)}
          </select>
        </Sub>
        <Sub label="Shields" hint="Enable Prompt Shields for jailbreak / indirect attacks.">
          <div className="flex items-center gap-3 px-3 h-9 rounded-xl bg-app border border-border text-xs">
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={!!cfg.shields?.jailbreak}
              onChange={(e) => set('shields')({ ...(cfg.shields || {}), jailbreak: e.target.checked })} /> jailbreak</label>
            <label className="inline-flex items-center gap-1"><input type="checkbox" checked={!!cfg.shields?.indirect}
              onChange={(e) => set('shields')({ ...(cfg.shields || {}), indirect: e.target.checked })} /> indirect</label>
          </div>
        </Sub>
      </div>
      <Hint>Live runtime support arrives when an Azure account is provided. The configuration UI is fully editable now.</Hint>
    </div>
  )
}

function Sub({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm text-gray-200 font-medium mb-1.5">{label}</div>
      {children}
      <div className="text-[11px] text-muted mt-1">{hint}</div>
    </div>
  )
}

function Hint({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] text-muted">{children}</div>
}
