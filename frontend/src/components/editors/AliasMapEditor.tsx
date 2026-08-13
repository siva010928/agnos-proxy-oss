// AliasMapEditor - manages MULTIPLE aliases, each with its own AliasTargetsBuilder.
// Used by Components and the workspace-level chat_models in the wizard.
//
// Each alias is collapsible; the active alias's targets editor is open.
// "+ Add alias" creates a new entry, prompts for the name once (validated as
// a slug), and opens it.

import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import React, { useState } from 'react'
import { AliasTarget, AliasTargetsBuilder, targetsValid } from './AliasTargetsBuilder'

export type AliasMap = Record<string, AliasTarget[]>

// Alias names mirror model-id conventions, which legitimately contain dots
// (e.g. gemini-3.1-pro-preview) and underscores. Keep this in sync with the
// backend (non-empty string) and Routing.tsx - do not be stricter.
const SLUG_RE = /^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/

export function AliasMapEditor({
  workspaceId,
  value,
  onChange,
  testIdPrefix = 'alias-map',
  emptyHint,
}: {
  workspaceId: string | null
  value: AliasMap
  onChange: (next: AliasMap) => void
  testIdPrefix?: string
  emptyHint?: React.ReactNode
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)
  const [newAlias, setNewAlias] = useState('')
  const [aliasErr, setAliasErr] = useState<string | null>(null)

  const aliases = Object.keys(value)
  const toggle = (a: string) => {
    setOpen((s) => {
      const n = new Set(s)
      n.has(a) ? n.delete(a) : n.add(a)
      return n
    })
  }

  const addAlias = () => {
    const a = newAlias.trim().toLowerCase()
    if (!a) {
      setAliasErr('Alias name is required.')
      return
    }
    if (!SLUG_RE.test(a)) {
      setAliasErr('Alias must be lowercase letters, digits, hyphens, dots or underscores (no leading/trailing dash).')
      return
    }
    if (value[a]) {
      setAliasErr(`Alias '${a}' already exists.`)
      return
    }
    onChange({ ...value, [a]: [] })
    setOpen((s) => new Set([...s, a]))
    setAdding(false)
    setNewAlias('')
    setAliasErr(null)
  }

  const removeAlias = (a: string) => {
    const next = { ...value }
    delete next[a]
    onChange(next)
  }

  const setTargets = (a: string, t: AliasTarget[]) => {
    onChange({ ...value, [a]: t })
  }

  return (
    <div className="space-y-2" data-testid={testIdPrefix}>
      {aliases.length === 0 && !adding && (
        <div className="bg-app/50 border border-dashed border-border rounded-lg p-4 text-center">
          <div className="text-[12px] text-muted">
            {emptyHint || 'No aliases yet. An alias is the model name your code uses (e.g. ' +
                         'claude-sonnet-4-5) that resolves to one or more concrete provider models.'}
          </div>
          <button type="button" className="btn-primary text-xs mt-3"
                  onClick={() => setAdding(true)}
                  data-testid={`${testIdPrefix}-add-first`}>
            <Plus size={12} /> Add your first alias
          </button>
        </div>
      )}

      {aliases.map((a) => {
        const targets = value[a] || []
        const isOpen = open.has(a)
        const v = targetsValid(targets)
        return (
          <div key={a} className="bg-app border border-border rounded-xl"
               data-testid={`${testIdPrefix}-alias-${a}`}>
            <div className="flex items-center gap-2 px-3 py-2">
              <button type="button" onClick={() => toggle(a)} className="text-muted hover:text-white"
                      aria-label={isOpen ? 'Collapse' : 'Expand'}
                      data-testid={`${testIdPrefix}-toggle-${a}`}>
                {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>
              <span className="mono text-xs text-gray-100 font-medium flex-1 truncate">{a}</span>
              <span className="text-[10.5px] text-muted">
                {targets.length} target{targets.length === 1 ? '' : 's'}
              </span>
              {!v.ok && (
                <span className="text-[10px] text-danger">⚠ incomplete</span>
              )}
              <button type="button"
                      className="text-danger p-1 hover:bg-danger/10 rounded"
                      onClick={() => removeAlias(a)}
                      aria-label={`Remove alias ${a}`}
                      data-testid={`${testIdPrefix}-remove-${a}`}>
                <Trash2 size={13} />
              </button>
            </div>
            {isOpen && (
              <div className="px-3 pb-3 pt-1 border-t border-border/60">
                <AliasTargetsBuilder
                  workspaceId={workspaceId}
                  targets={targets}
                  onChange={(t) => setTargets(a, t)}
                  testIdPrefix={`${testIdPrefix}-${a}`}
                  showHelp={false}
                />
              </div>
            )}
          </div>
        )
      })}

      {adding ? (
        <div className="bg-app border border-border rounded-xl p-3 space-y-2"
             data-testid={`${testIdPrefix}-add-form`}>
          <div className="text-[11.5px] text-gray-200">
            New alias name:
          </div>
          <input
            autoFocus
            className="input mono text-xs"
            value={newAlias}
            onChange={(e) => { setNewAlias(e.target.value); setAliasErr(null) }}
            placeholder="claude-sonnet-4-5"
            onKeyDown={(e) => {
              if (e.key === 'Enter') addAlias()
              if (e.key === 'Escape') { setAdding(false); setNewAlias(''); setAliasErr(null) }
            }}
            data-testid={`${testIdPrefix}-add-input`}
          />
          {aliasErr && <div className="text-[11px] text-danger">{aliasErr}</div>}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost text-xs"
                    onClick={() => { setAdding(false); setNewAlias(''); setAliasErr(null) }}>
              Cancel
            </button>
            <button type="button" className="btn-primary text-xs"
                    onClick={addAlias}
                    data-testid={`${testIdPrefix}-add-confirm`}>
              Add alias
            </button>
          </div>
        </div>
      ) : aliases.length > 0 ? (
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={() => setAdding(true)}
          data-testid={`${testIdPrefix}-add`}
        >
          <Plus size={12} /> Add another alias
        </button>
      ) : null}
    </div>
  )
}
