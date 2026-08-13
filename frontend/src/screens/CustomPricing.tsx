// Admin → Custom Pricing screen (rebuilt for WAVE 16-UX-2).
// - PricingOverrideForm replaces the bespoke editor: model picker (datalist
//   from /admin/models), step 0.000001, live "matches N models" feedback,
//   collision warning when too broad, rejects empty/short substrings.
// - Source pill explains synced|override|builtin|none.
// - Row ⋯ menu via portal RowMenu; ConfirmModal for delete.

import { motion, AnimatePresence } from 'framer-motion'
import {
  CircleDollarSign, Loader2, Pencil, Plus, RefreshCcw,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { admin } from '../api/client'
import { useModels } from '../lib/api'
import {
  ConfirmModal, Field, Modal, PricingOverrideForm, PricingOverrideValue,
  RowMenu, emptyPricingOverride, pricingOverrideValid,
} from '../components/editors'
import { toastError, toastOk, withToast } from '../components/Toast'
import { Card, EmptyState, Pill, ProviderBadge, Skeleton, SearchInput } from '../components/ui'
import { useCurrency } from '../lib/currency'
import { ChevronDown, ChevronUp } from 'lucide-react'

interface ModelRow {
  provider: string
  model_id: string
  context_window: number
  input_per_1k: number
  output_per_1k: number
  price_source: 'override' | 'synced' | 'builtin' | 'none'
  used_by?: string[]
}

interface OverrideRow {
  id: number
  model_substr: string
  input_per_1k: number
  output_per_1k: number
  note: string
}

const SOURCE_COLOR: Record<string, string> = {
  override: '#FBBF24',
  synced: '#34D399',
  builtin: '#A78BFA',
  none: '#6B7280',
}

const SOURCE_HINT: Record<string, string> = {
  override: 'Custom price set on this page',
  synced: 'From the LiteLLM-synced public dataset',
  builtin: 'Hard-coded fallback in the gateway',
  none: 'Unknown - cost will record as 0',
}

export function CustomPricing() {
  const models = useModels()
  const [overrides, setOverrides] = useState<OverrideRow[]>([])
  const [loadingOv, setLoadingOv] = useState(false)
  const [filter, setFilter] = useState('')
  const [editorValue, setEditorValue] = useState<PricingOverrideValue>(emptyPricingOverride())
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorIsEdit, setEditorIsEdit] = useState(false)
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState<OverrideRow | null>(null)
  const { convert, symbol } = useCurrency()
  const [providerFilter, setProviderFilter] = useState('')
  const [unit, setUnit] = useState<1000 | 1000000>(1_000_000)   // per-1M default (clearer)
  const [sortKey, setSortKey] = useState<'model' | 'input' | 'output'>('model')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  // price per selected unit, converted to the active currency
  const priceStr = (per1k: number) => {
    const v = convert((per1k || 0) * (unit / 1000))
    return `${symbol}${v.toFixed(v >= 1 ? 2 : v >= 0.001 ? 4 : 6)}`
  }
  const toggleSort = (k: 'model' | 'input' | 'output') =>
    sortKey === k ? setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')) : (setSortKey(k), setSortDir('asc'))

  const loadOverrides = async () => {
    setLoadingOv(true)
    try {
      const r = await admin.listPricing()
      setOverrides((r.overrides || []) as OverrideRow[])
    } catch (e: any) {
      toastError(e?.message || 'failed to load overrides')
    } finally {
      setLoadingOv(false)
    }
  }
  useEffect(() => { loadOverrides() }, [])

  const list: ModelRow[] = (models.data?.models as any) || []
  const providers = useMemo(() => Array.from(new Set(list.map((m) => m.provider))).sort(), [list])
  // hyphen/space/underscore-insensitive substring match, so "claude sonnet 45"
  // finds "claude-sonnet-4-5" (the old exact-substring search made hyphens painful).
  const norm = (s: string) => (s || '').toLowerCase().replace(/[-_.\s]/g, '')
  const filtered = useMemo(() => {
    let out = list
    if (providerFilter) out = out.filter((m) => m.provider === providerFilter)
    if (filter.trim()) {
      const q = norm(filter)
      out = out.filter((m) => norm(m.model_id).includes(q) ||
        m.provider.toLowerCase().includes(filter.trim().toLowerCase()))
    }
    const dir = sortDir === 'asc' ? 1 : -1
    return [...out].sort((a, b) => {
      if (sortKey === 'input') return ((a.input_per_1k || 0) - (b.input_per_1k || 0)) * dir
      if (sortKey === 'output') return ((a.output_per_1k || 0) - (b.output_per_1k || 0)) * dir
      return a.model_id.localeCompare(b.model_id) * dir
    })
  }, [list, filter, providerFilter, sortKey, sortDir])

  function openNew() {
    setEditorIsEdit(false)
    setEditorValue(emptyPricingOverride())
    setEditorOpen(true)
  }
  function openEdit(o: OverrideRow) {
    setEditorIsEdit(true)
    setEditorValue({
      model_substr: o.model_substr,
      input_per_1k: o.input_per_1k,
      output_per_1k: o.output_per_1k,
      note: o.note || '',
    })
    setEditorOpen(true)
  }

  async function save() {
    const v = pricingOverrideValid(editorValue)
    if (!v.ok) {
      toastError(v.errors[0])
      return
    }
    setBusy(true)
    try {
      await admin.upsertPricing(editorValue as any)
      toastOk(`Override for '${editorValue.model_substr}' saved`)
      setEditorOpen(false)
      await Promise.all([loadOverrides(), models.refetch()])
    } catch (e: any) {
      toastError(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Admin · Custom Pricing</h1>
          <p className="text-muted text-sm">
            Per-model price overrides used to compute <span className="mono">cost_usd</span> on every request.
            An override matches by case-insensitive substring of <span className="mono">model_id</span> and
            wins over the synced LiteLLM dataset. Deleting an override reverts to the synced price.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost text-xs"
            onClick={() => Promise.all([loadOverrides(), models.refetch()])}
            data-testid="pricing-refresh"
          >
            <RefreshCcw size={12} /> Refresh
          </button>
          <button data-testid="pricing-new" className="btn-primary" onClick={openNew}>
            <Plus size={16} /> Add override
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
        <Card className="p-0">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap">
            <select value={providerFilter} onChange={(e) => setProviderFilter(e.target.value)}
                    data-testid="pricing-provider"
                    className="bg-app border border-border rounded-lg px-2 py-1.5 text-sm text-gray-200">
              <option value="">All providers</option>
              {providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <SearchInput
              value={filter}
              onChange={setFilter}
              placeholder="Search model (hyphens optional) or provider…"
              className="flex-1 min-w-[180px]"
              testId="pricing-filter"
            />
            <div className="flex rounded-lg overflow-hidden border border-border" data-testid="pricing-unit">
              {([[1000, 'per 1K'], [1_000_000, 'per 1M']] as const).map(([u, lbl]) => (
                <button key={u} onClick={() => setUnit(u)}
                        className={`px-2.5 py-1.5 text-[11px] ${unit === u ? 'bg-accent text-white' : 'text-muted hover:text-gray-200'}`}>
                  {lbl}
                </button>
              ))}
            </div>
            <span className="text-[11px] text-muted whitespace-nowrap">{filtered.length} / {list.length}</span>
          </div>
          {models.isLoading ? (
            <div className="p-6"><Skeleton h={140} /></div>
          ) : filtered.length === 0 ? (
            <div className="p-10">
              <EmptyState
                icon={<CircleDollarSign size={32} />}
                title="No models match the filter"
                hint="The catalog is auto-synced from LiteLLM at startup. Clear the filter to see all models."
              />
            </div>
          ) : (
            <>
            {/* sortable column header */}
            <div className="px-4 py-1.5 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted border-b border-border bg-app/40 select-none">
              <button onClick={() => toggleSort('model')} className="flex-1 text-left inline-flex items-center gap-1 hover:text-gray-200" data-testid="pricing-sort-model">
                Model {sortKey === 'model' && (sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
              </button>
              <button onClick={() => toggleSort('input')} className="w-[110px] text-right inline-flex items-center justify-end gap-1 hover:text-gray-200" data-testid="pricing-sort-input">
                Input /{unit === 1000 ? '1K' : '1M'} {sortKey === 'input' && (sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
              </button>
              <button onClick={() => toggleSort('output')} className="w-[110px] text-right inline-flex items-center justify-end gap-1 hover:text-gray-200" data-testid="pricing-sort-output">
                Output /{unit === 1000 ? '1K' : '1M'} {sortKey === 'output' && (sortDir === 'asc' ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
              </button>
              <span className="w-[64px] text-right">Source</span>
            </div>
            <div className="divide-y divide-border max-h-[60vh] overflow-y-auto" data-testid="pricing-list">
              {filtered.map((m) => (
                <div
                  key={`${m.provider}/${m.model_id}`}
                  className="px-4 py-2.5 flex items-center gap-2 hover:bg-elevated/30"
                  data-testid={`model-row-${m.provider}-${m.model_id.slice(0, 24)}`}
                >
                  <ProviderBadge provider={m.provider} />
                  <span className="mono text-[11.5px] text-gray-100 flex-1 truncate" title={m.model_id}>
                    {m.model_id}
                    {(m.used_by?.length ?? 0) > 0 && (
                      <span className="ml-2 text-[9px] uppercase tracking-wider text-accent/80"
                            title={`configured in ${m.used_by!.length} workspace target(s)`}>in use</span>
                    )}
                  </span>
                  <span className="w-[110px] text-right mono text-[11.5px] text-gray-200 tabular-nums border-l border-border/60 pl-2">
                    {priceStr(m.input_per_1k)}
                  </span>
                  <span className="w-[110px] text-right mono text-[11.5px] text-gray-200 tabular-nums border-l border-border/60 pl-2">
                    {priceStr(m.output_per_1k)}
                  </span>
                  {/* Only chip the anomalies - synced is the default/expected state,
                      so chipping every row was pure noise. */}
                  <span className="w-[64px] text-right">
                  {m.price_source === 'synced' ? (
                    <span className="text-[10px] text-muted/70" title={SOURCE_HINT.synced}>synced</span>
                  ) : (
                    <Pill color={SOURCE_COLOR[m.price_source]} title={SOURCE_HINT[m.price_source]}>
                      {m.price_source}
                    </Pill>
                  )}
                  </span>
                </div>
              ))}
            </div>
            </>
          )}
        </Card>

        {/* Active overrides side panel */}
        <Card className="p-0">
          <div className="px-5 py-4 border-b border-border">
            <div className="text-sm font-semibold text-gray-100">Active overrides</div>
            <div className="text-[11px] text-muted mt-0.5">
              {overrides.length} active. An override wins over the synced price for any matching model.
            </div>
          </div>
          {loadingOv ? (
            <div className="p-5"><Skeleton h={80} /></div>
          ) : overrides.length === 0 ? (
            <div className="p-6 text-center">
              <div className="text-muted text-[12px] mb-3">
                No overrides yet. Set a custom price for any model whose <span className="mono">model_id</span> matches a substring.
              </div>
              <button data-testid="pricing-new-empty" className="btn-primary text-sm mx-auto" onClick={openNew}>
                <Plus size={15} /> Add override
              </button>
            </div>
          ) : (
            <div className="divide-y divide-border" data-testid="override-list">
              {overrides.map((o) => (
                <div
                  key={o.id}
                  className="px-5 py-3 hover:bg-elevated/30 flex items-center gap-2"
                  data-testid={`override-row-${o.model_substr}`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="mono text-[12px] text-gray-100 truncate">{o.model_substr}</div>
                    <div className="text-[10.5px] text-muted">
                      {priceStr(o.input_per_1k)} in · {priceStr(o.output_per_1k)} out
                      <span className="opacity-70"> /{unit === 1000 ? '1K' : '1M'}</span>
                    </div>
                    {o.note && <div className="text-[10.5px] text-muted truncate">{o.note}</div>}
                  </div>
                  <RowMenu
                    testId={`override-menu-${o.model_substr}`}
                    items={[
                      {
                        label: 'Edit',
                        icon: <Pencil size={13} />,
                        onSelect: () => openEdit(o),
                        testId: `override-edit-${o.model_substr}`,
                      },
                      {
                        label: 'Delete',
                        danger: true,
                        onSelect: () => setConfirmDel(o),
                        testId: `override-delete-${o.model_substr}`,
                      },
                    ]}
                  />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Modal
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        title={editorIsEdit ? `Edit override · ${editorValue.model_substr}` : 'New pricing override'}
        size="md"
        testId="pricing-editor"
        footer={
          <>
            <button className="btn-ghost text-sm" onClick={() => setEditorOpen(false)} disabled={busy}>
              Cancel
            </button>
            <button
              className="btn-primary text-sm disabled:opacity-50"
              disabled={busy || !pricingOverrideValid(editorValue).ok}
              onClick={save}
              data-testid="pricing-save"
            >
              {busy ? <><Loader2 size={14} className="animate-spin" /> Saving…</> : 'Save'}
            </button>
          </>
        }
      >
        <PricingOverrideForm
          value={editorValue}
          onChange={setEditorValue}
          isEdit={editorIsEdit}
          testIdPrefix="pricing"
        />
      </Modal>

      <ConfirmModal
        open={!!confirmDel}
        onCancel={() => setConfirmDel(null)}
        title={`Delete override '${confirmDel?.model_substr}'?`}
        message="The synced LiteLLM price (or builtin fallback) will be used for matching models again."
        identifier={confirmDel ? `model_substr='${confirmDel.model_substr}'` : null}
        confirmLabel="Delete override"
        danger
        onConfirm={async () => {
          if (!confirmDel) return
          await withToast(async () => {
            await admin.deletePricing(confirmDel.model_substr)
            await Promise.all([loadOverrides(), models.refetch()])
          })
          setConfirmDel(null)
        }}
        testId="pricing-confirm-delete"
      />
    </div>
  )
}
