// Visual Rule Builder + live CEL preview. Recursive groups, AND/OR toggle,
// per-condition Attribute/Operator/Value rows. Generates the CEL the backend
// celpy evaluator runs against our request context.

import { useEffect, useMemo, useState } from 'react'
import { Plus, Copy, X, Layers } from 'lucide-react'
import {
  Attr, Cond, Group, Node, Op,
  ATTR_LABELS, OP_LABELS, opsForAttr, newCondition, newGroup,
  buildCel, treeComplete,
} from '../lib/celBuilder'
import { admin } from '../api/client'
import { toastError, toastOk } from './Toast'

const ATTRS: Attr[] = ['model', 'header', 'workspace', 'component', 'user', 'role', 'message_contains', 'message_length']

interface Props {
  value: Group
  onChange: (g: Group) => void
  /** Read-only visualization (e.g. when user is editing raw CEL). */
  disabled?: boolean
}

export function RuleBuilder({ value, onChange, disabled }: Props) {
  const cel = useMemo(() => buildCel(value), [value])
  const complete = treeComplete(value)

  function update(path: number[], updater: (n: Node) => Node) {
    const clone: Group = JSON.parse(JSON.stringify(value))
    let parent: Group = clone
    for (let i = 0; i < path.length - 1; i++) parent = parent.children[path[i]] as Group
    if (path.length === 0) onChange(updater(clone) as Group)
    else {
      parent.children[path[path.length - 1]] = updater(parent.children[path[path.length - 1]])
      onChange(clone)
    }
  }

  function addCondition(path: number[]) {
    update(path, (g) => ({ ...(g as Group), children: [...(g as Group).children, newCondition()] }))
  }
  function addGroup(path: number[]) {
    update(path, (g) => ({ ...(g as Group), children: [...(g as Group).children, newGroup('and')] }))
  }
  function remove(path: number[]) {
    if (path.length === 0) return
    const clone: Group = JSON.parse(JSON.stringify(value))
    let parent: Group = clone
    for (let i = 0; i < path.length - 1; i++) parent = parent.children[path[i]] as Group
    parent.children.splice(path[path.length - 1], 1)
    onChange(clone)
  }

  return (
    <div className="space-y-3" data-testid="rule-builder">
      <div className="text-[11px] text-muted">
        Describe <span className="text-gray-300">when</span> this rule should apply, in plain language. Each row is a
        criterion: <span className="text-gray-300">context attribute → operator → value</span>. Leave it empty to apply
        to every request at the sampling rate.
      </div>

      <GroupNode g={value} path={[]} update={update}
                 addCondition={addCondition} addGroup={addGroup} remove={remove}
                 disabled={disabled} />

      <CelPreview cel={cel} complete={complete} />
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────

function GroupNode({ g, path, update, addCondition, addGroup, remove, disabled }: {
  g: Group; path: number[]
  update: (p: number[], u: (n: Node) => Node) => void
  addCondition: (p: number[]) => void
  addGroup: (p: number[]) => void
  remove: (p: number[]) => void
  disabled?: boolean
}) {
  return (
    <div className="rounded-xl border border-border bg-app/40 p-3" data-testid="rule-group">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        {/* Plain-English combinator: reads like a sentence, not logical shorthand */}
        <div className="flex items-center gap-2">
          <span className="text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>Match traffic when</span>
          <div className="flex bg-elevated rounded-lg p-0.5" data-testid="combinator" role="radiogroup" aria-label="condition combinator">
            {(['and', 'or'] as const).map((c) => (
              <button key={c} type="button" disabled={disabled} data-testid={`combinator-${c}`}
                role="radio" aria-checked={g.combinator === c}
                onClick={() => update(path, (n) => ({ ...(n as Group), combinator: c }))}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  g.combinator === c ? 'bg-accent text-white' : 'text-muted hover:text-gray-200'}`}>
                {c === 'and' ? 'ALL conditions are met' : 'ANY condition is met'}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" disabled={disabled} data-testid="add-condition"
            onClick={() => addCondition(path)}
            className="text-[11px] text-muted hover:text-gray-200 inline-flex items-center gap-1 px-2 py-1 rounded">
            <Plus size={11} /> Add criteria row
          </button>
          <button type="button" disabled={disabled} data-testid="add-group"
            onClick={() => addGroup(path)}
            className="text-[11px] text-muted hover:text-gray-200 inline-flex items-center gap-1 px-2 py-1 rounded">
            <Layers size={11} /> Add group
          </button>
          {path.length > 0 && (
            <button type="button" disabled={disabled} onClick={() => remove(path)}
              className="text-danger ml-1"><X size={13} /></button>
          )}
        </div>
      </div>
      {g.children.length === 0 ? (
        <div className="text-[11px] text-muted py-2 px-1 italic">No criteria yet → this rule applies to every request (at the sampling rate).</div>
      ) : (
        <div className="space-y-1.5">
          {g.children.map((c, i) => (
            <div key={i}>
              {i > 0 && (
                <div className="flex items-center gap-2 py-0.5 pl-1" aria-hidden>
                  <span className="text-[9.5px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded"
                        style={{ background: 'var(--color-accent-soft)', color: 'var(--color-accent)' }}>
                    {g.combinator === 'and' ? 'AND' : 'OR'}
                  </span>
                  <span className="h-px flex-1" style={{ background: 'var(--color-border)' }} />
                </div>
              )}
              {c.type === 'cond' ? (
                <ConditionRow c={c} path={[...path, i]} update={update} remove={remove} disabled={disabled} />
              ) : (
                <GroupNode g={c} path={[...path, i]}
                           update={update} addCondition={addCondition} addGroup={addGroup} remove={remove}
                           disabled={disabled} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ConditionRow({ c, path, update, remove, disabled }: {
  c: Cond; path: number[]
  update: (p: number[], u: (n: Node) => Node) => void
  remove: (p: number[]) => void
  disabled?: boolean
}) {
  const ops = opsForAttr(c.attribute)
  // make sure operator stays valid when attribute changes
  function setAttr(a: Attr) {
    const allowed = opsForAttr(a)
    const op = allowed.includes(c.operator) ? c.operator : allowed[0]
    update(path, () => ({ ...c, attribute: a, operator: op } as Cond))
  }
  const tid = `cond-${path.join('-')}`
  const showHeader = c.attribute === 'header'
  const showValue = c.operator !== 'has_value'
  return (
    <div className="grid grid-cols-[1fr_auto_1fr_auto_1fr_auto] items-center gap-2" data-testid={tid}>
      <select disabled={disabled} className="input py-1.5 text-xs" data-testid={`${tid}-attr`}
        value={c.attribute} onChange={(e) => setAttr(e.target.value as Attr)}>
        {ATTRS.map((a) => <option key={a} value={a}>{ATTR_LABELS[a]}</option>)}
      </select>
      {showHeader ? (
        <input disabled={disabled} className="input py-1.5 text-xs w-32" data-testid={`${tid}-header`}
          placeholder="header name" value={c.header_name || ''}
          onChange={(e) => update(path, () => ({ ...c, header_name: e.target.value } as Cond))} />
      ) : <span />}
      <select disabled={disabled} className="input py-1.5 text-xs" data-testid={`${tid}-op`}
        value={c.operator} onChange={(e) => update(path, () => ({ ...c, operator: e.target.value as Op } as Cond))}>
        {ops.map((o) => <option key={o} value={o}>{OP_LABELS[o]}</option>)}
      </select>
      {showValue ? (
        <input disabled={disabled} className="input py-1.5 text-xs" data-testid={`${tid}-value`}
          placeholder={c.operator === 'in' ? 'a, b, c' : 'value'} value={c.value}
          onChange={(e) => update(path, () => ({ ...c, value: e.target.value } as Cond))} />
      ) : <span className="text-[11px] text-muted">(no value)</span>}
      <span />
      <button type="button" disabled={disabled} onClick={() => remove(path)}
        className="text-danger" data-testid={`${tid}-delete`}><X size={13} /></button>
    </div>
  )
}

function CelPreview({ cel, complete }: { cel: string; complete: boolean }) {
  const [valid, setValid] = useState<{ ok: boolean; error?: string } | null>(null)
  const [busy, setBusy] = useState(false)

  // debounced server-side validate
  useEffect(() => {
    const id = window.setTimeout(async () => {
      if (!complete) { setValid(null); return }
      try {
        const r = await admin.validateCel(cel)
        setValid({ ok: !!r.ok, error: r.error })
      } catch { setValid(null) }
    }, 350)
    return () => window.clearTimeout(id)
  }, [cel, complete])

  async function copy() {
    setBusy(true)
    try { await navigator.clipboard?.writeText(cel); toastOk('CEL copied') }
    catch { toastError('clipboard unavailable') } finally { setBusy(false) }
  }

  return (
    <div className="rounded-xl border border-border" style={{ background: 'var(--color-code-bg)' }}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="text-[11px] uppercase tracking-wider text-muted">Parsed evaluation preview</div>
          <span className="text-[10px] text-muted/70">- auto-generated CEL (developer cross-reference)</span>
        </div>
        <div className="flex items-center gap-2">
          {valid?.ok && <span className="text-[11px] text-ok" data-testid="cel-valid">✓ valid</span>}
          {valid && !valid.ok && <span className="text-[11px] text-danger" data-testid="cel-invalid">✕ {valid.error?.slice(0, 60)}</span>}
          <button type="button" onClick={copy} disabled={busy}
                  className="text-[11px] text-muted hover:text-gray-200 inline-flex items-center gap-1">
            <Copy size={11} /> copy
          </button>
        </div>
      </div>
      <pre className="px-3 py-2.5 text-xs mono overflow-x-auto whitespace-pre-wrap"
           style={{ color: 'var(--color-code-text)' }}
           data-testid="cel-preview">{cel}</pre>
    </div>
  )
}
