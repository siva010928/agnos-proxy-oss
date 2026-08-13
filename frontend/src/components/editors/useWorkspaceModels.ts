// Hooks/helpers used by the alias→targets builder. Returns the catalog of
// models available to a workspace = models from /admin/models filtered to
// providers actually configured for THIS workspace (avoids the "I selected
// bedrock but the workspace has no bedrock creds" footgun).
//
// Falls back to the full catalog if `/admin/workspaces/{id}/providers` hasn't
// loaded yet, so the user always sees something rather than an empty
// dropdown.
import { useQueries, useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

export interface ModelEntry {
  provider: string
  model_id: string
  context_window?: number
  used_by?: string[]
}

export function useAdminModels() {
  return useQuery<{ models: ModelEntry[] }>({
    queryKey: ['admin-models'],
    queryFn: () => api('/admin/models'),
    staleTime: 30_000,
  })
}



export function useWorkspaceProviders(workspaceId: string | null) {
  return useQuery<{ providers: { provider: string }[] }>({
    queryKey: ['ws-providers', workspaceId],
    queryFn: () => api(`/admin/workspaces/${workspaceId}/providers`),
    enabled: !!workspaceId,
    staleTime: 10_000,
  })
}

export interface WorkspaceModelOption {
  provider: string
  model_id: string
  label: string                    // "bedrock · us.anthropic.claude-sonnet-4-5-…"
  context_window?: number
}

export function useWorkspaceModels(workspaceId: string | null): {
  options: WorkspaceModelOption[]
  byProvider: Record<string, WorkspaceModelOption[]>
  configuredProviders: string[]
  liveProviders: Set<string>   // providers whose models come from the LIVE account
  isLoading: boolean
} {
  const models = useAdminModels()
  const wsProv = useWorkspaceProviders(workspaceId)
  const list = models.data?.models || []
  const configured = (wsProv.data?.providers || []).map((p) => p.provider)

  // Live per-account model availability: for each configured provider, query the
  // real account for the models it can actually reach (bedrock inference profiles
  // per auth mode, OpenAI/Gemini /models, ...). Restrict selection to those; fall
  // back to the global catalog when the account can't be listed (e.g. a bearer/
  // guardrail-only principal), so the dropdown is never empty.
  const availQueries = useQueries({
    queries: configured.map((p) => ({
      queryKey: ['avail-models', workspaceId, p],
      queryFn: () => api(`/admin/workspaces/${workspaceId}/providers/${p}/available-models`),
      enabled: !!workspaceId,
      staleTime: 60_000,
    })),
  })
  const liveByProvider: Record<string, string[]> = {}
  const liveProviders = new Set<string>()
  configured.forEach((p, i) => {
    const d = availQueries[i]?.data as { ok?: boolean; models?: string[] } | undefined
    if (d?.ok && Array.isArray(d.models) && d.models.length) {
      liveByProvider[p] = d.models
      liveProviders.add(p)
    }
  })

  // catalog fallback (global /admin/models filtered to configured providers)
  const catalogByProvider: Record<string, WorkspaceModelOption[]> = {}
  for (const m of list) {
    if (configured.length > 0 && !configured.includes(m.provider)) continue
    ;(catalogByProvider[m.provider] ||= []).push({
      provider: m.provider, model_id: m.model_id,
      label: `${m.provider} · ${m.model_id}`, context_window: m.context_window,
    })
  }

  const byProvider: Record<string, WorkspaceModelOption[]> = {}
  const scopeProviders = configured.length > 0 ? configured : Object.keys(catalogByProvider)
  for (const p of scopeProviders) {
    byProvider[p] = liveByProvider[p]
      ? liveByProvider[p].map((id) => ({ provider: p, model_id: id, label: `${p} · ${id}` }))
      : (catalogByProvider[p] || [])
  }
  const opts = Object.values(byProvider).flat()

  return {
    options: opts,
    byProvider,
    configuredProviders: configured,
    liveProviders,
    isLoading: models.isLoading || wsProv.isLoading,
  }
}
