import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import {
  mapApiPublisherGroup,
  type ApiPublisherGroup,
  type UiPublisherGroup as CompanyGroup,
} from '../api/publisherCrm'
import { toWorkspaceSearchString } from '../lib/workspaceUrl'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useDebounced } from '../hooks/useDebounced'
import { CrmResearchDot } from '../ui/CrmResearchDot'
import { CrmResearchTh } from '../ui/CrmResearchTh'

function fmtVisits(v: number | null | undefined) {
  const n = typeof v === 'number' ? v : Number(v || 0)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace('.0', '')}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1).replace('.0', '')}K`
  return `${Math.round(n)}`
}

function formatSpocSummary(spocs: { name: string }[]): string {
  const cleaned = (spocs || [])
    .map((s) => (s?.name || '').trim())
    .filter((n) => n && n !== '—')
  if (cleaned.length === 0) return '-'
  if (cleaned.length === 1) return cleaned[0]
  return `${cleaned[0]} +${cleaned.length - 1}`
}

const SYND_BADGE: Record<string, { label: string; bg: string; color: string }> = {
  msn: { label: 'MSN', bg: '#DBEAFE', color: '#1E40AF' },
  yahoo: { label: 'Yahoo', bg: '#EDE9FE', color: '#6D28D9' },
  newsbreak: { label: 'NewsBreak', bg: '#D1FAE5', color: '#065F46' },
  smartnews: { label: 'Smartnews', bg: '#F3F4F6', color: '#374151' },
}

function SyndicationBadges({ syndication }: { syndication: string[] | undefined | null }) {
  const list = syndication || []
  if (!list.length) return <span className="muted">None</span>
  return (
    <div className="crm-synd-badges">
      {list.map((s) => {
        const cfg = SYND_BADGE[s] || { label: s, bg: '#F3F4F6', color: '#374151' }
        return (
          <span key={s} className="crm-synd-badge" style={{ background: cfg.bg, color: cfg.color }}>
            {cfg.label}
          </span>
        )
      })}
    </div>
  )
}

const DEFAULT_PAGE_SIZE = 50
const MIN_PAGE_SIZE = 10
const MAX_PAGE_SIZE = 200
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100, 200] as const

function clampPageSize(n: number): number {
  if (!Number.isFinite(n)) return DEFAULT_PAGE_SIZE
  const r = Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, Math.round(n)))
  let best: (typeof PAGE_SIZE_OPTIONS)[number] = PAGE_SIZE_OPTIONS[1]
  let bestDiff = Infinity
  for (const o of PAGE_SIZE_OPTIONS) {
    const d = Math.abs(o - r)
    if (d < bestDiff) {
      best = o
      bestDiff = d
    }
  }
  return best
}

function companiesUrlParamsEqual(a: URLSearchParams, b: URLSearchParams): boolean {
  const keys = new Set<string>([...a.keys(), ...b.keys()])
  for (const k of keys) {
    if (a.get(k) !== b.get(k)) return false
  }
  return true
}

function parseCompaniesUrlSearch(search: string): {
  source: 'independent' | 'media'
  page: number
  limit: number
  q: string
} {
  const raw = search.startsWith('?') ? search.slice(1) : search
  const sp = new URLSearchParams(raw)
  const source: 'independent' | 'media' = sp.get('source') === 'media' ? 'media' : 'independent'
  const page = Math.max(1, parseInt(sp.get('page') || '1', 10) || 1)
  const limit = clampPageSize(parseInt(sp.get('limit') || String(DEFAULT_PAGE_SIZE), 10) || DEFAULT_PAGE_SIZE)
  const q = sp.get('q') ?? ''
  return { source, page, limit, q }
}

export default function CompaniesPage() {
  const { selection, setSelection } = useSelection()
  const meta = useMeta()
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [initialUrl] = useState(() =>
    parseCompaniesUrlSearch(typeof window !== 'undefined' ? window.location.search : ''),
  )

  const [crmSource, setCrmSource] = useState<'independent' | 'media'>(initialUrl.source)
  const [listPage, setListPage] = useState(initialUrl.page)
  const [pageSize, setPageSize] = useState(initialUrl.limit)
  const [q, setQ] = useState(initialUrl.q)
  const dq = useDebounced(q, 280)

  const [companyGroups, setCompanyGroups] = useState<CompanyGroup[]>([])
  const [listTotal, setListTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [expandedMediaHouseKey, setExpandedMediaHouseKey] = useState<string | null>(null)
  const [mediaBrandsLoadingKey, setMediaBrandsLoadingKey] = useState<string | null>(null)
  const [listEpoch, setListEpoch] = useState(0)
  const [retagOpen, setRetagOpen] = useState(false)
  const [retagSourceType, setRetagSourceType] = useState<'independent' | 'media_house'>('independent')
  const [retagSourceId, setRetagSourceId] = useState<number | null>(null)
  const [retagLabel, setRetagLabel] = useState('')
  const [retagTargetType, setRetagTargetType] = useState<'independent' | 'media_house'>('media_house')
  const [retagParentName, setRetagParentName] = useState('')
  const [retagReason, setRetagReason] = useState('')
  const [addMediaOpen, setAddMediaOpen] = useState(false)
  const [addMediaParent, setAddMediaParent] = useState('')
  const [addMediaDomain, setAddMediaDomain] = useState('')
  const [mediaParentSuggestions, setMediaParentSuggestions] = useState<string[]>([])
  const companyGroupsRef = useRef(companyGroups)
  companyGroupsRef.current = companyGroups

  const prevFilterKey = useRef<string | null>(null)

  const statusOptions = meta?.contact_statuses || []

  const statusBuckets = useMemo(() => {
    const notContacted: string =
      statusOptions.find((s) => s === 'not_contacted') || statusOptions[0] || 'not_contacted'
    return { notContacted }
  }, [statusOptions])

  useEffect(() => {
    const fk = `${crmSource}|${dq.trim()}`
    if (prevFilterKey.current !== null && prevFilterKey.current !== fk) {
      setListPage(1)
    }
    prevFilterKey.current = fk
  }, [crmSource, dq])

  useEffect(() => {
    const hasScopedSel =
      searchParams.has('sel_company') || searchParams.has('sel_advisor') || searchParams.has('sel_contact') || searchParams.has('sel_session')
    const p = new URLSearchParams(searchParams)
    if (!hasScopedSel) p.set('source', crmSource)
    else p.delete('source')
    p.set('page', String(listPage))
    p.set('limit', String(pageSize))
    if (dq.trim()) p.set('q', dq.trim())
    else p.delete('q')
    if (companiesUrlParamsEqual(p, new URLSearchParams(searchParams))) return
    setSearchParams(p, { replace: true })
  }, [crmSource, listPage, pageSize, dq, searchParams, setSearchParams])

  useEffect(() => {
    const next = selection.source === 'media' ? 'media' : 'independent'
    if (next !== crmSource) setCrmSource(next)
  }, [selection.source, crmSource])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setErr('')
    ;(async () => {
      try {
        const qs = new URLSearchParams()
        qs.set('source', crmSource)
        qs.set('offer_tab', '')
        qs.set('page', String(listPage))
        qs.set('page_size', String(pageSize))
        qs.set('kpi', 'total')
        qs.set('due_dates', '')
        qs.set('compact', 'true')
        if (dq.trim()) qs.set('search', dq.trim())
        const out = await apiFetch<{
          kpis: Record<string, number>
          groups: ApiPublisherGroup[]
          total: number
          page: number
          page_size: number
        }>(`/api/dashboard/publisher-crm?${qs.toString()}`)
        if (cancelled) return
        setListTotal(Number(out.total ?? 0))
        setCompanyGroups((out.groups || []).map(mapApiPublisherGroup))
        setExpandedMediaHouseKey(null)
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Load failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [crmSource, dq, listPage, pageSize, listEpoch])

  useEffect(() => {
    const needsSuggestions = addMediaOpen || (retagOpen && retagTargetType === 'media_house')
    if (!needsSuggestions) return
    let cancelled = false
    ;(async () => {
      try {
        const rows = await apiFetch<{ parent_name: string }[]>('/api/advisor/media-houses', { silent: true })
        if (cancelled) return
        const byLower = new Map<string, string>()
        for (const r of rows || []) {
          const val = (r.parent_name || '').trim()
          if (!val) continue
          const k = val.toLowerCase()
          if (!byLower.has(k)) byLower.set(k, val)
        }
        setMediaParentSuggestions(Array.from(byLower.values()))
      } catch {
        if (!cancelled) setMediaParentSuggestions([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [addMediaOpen, retagOpen, retagTargetType])

  const filteredRetagParentSuggestions = useMemo(() => {
    const qtxt = retagParentName.trim().toLowerCase()
    const list = mediaParentSuggestions || []
    if (!qtxt) return list.slice(0, 12)
    return list.filter((p) => p.toLowerCase().includes(qtxt)).slice(0, 12)
  }, [mediaParentSuggestions, retagParentName])

  const hasExactRetagParentMatch = useMemo(() => {
    const qtxt = retagParentName.trim().toLowerCase()
    if (!qtxt) return false
    return (mediaParentSuggestions || []).some((p) => p.trim().toLowerCase() === qtxt)
  }, [mediaParentSuggestions, retagParentName])

  useEffect(() => {
    if (crmSource !== 'media' || !expandedMediaHouseKey) {
      setMediaBrandsLoadingKey(null)
      return
    }
    const g = companyGroupsRef.current.find((x) => x.key === expandedMediaHouseKey)
    if (!g) return
    const bc = g.brandCount ?? 0
    if ((g.brands?.length ?? 0) > 0 || bc === 0) {
      setMediaBrandsLoadingKey(null)
      return
    }
    let cancelled = false
    setMediaBrandsLoadingKey(expandedMediaHouseKey)
    ;(async () => {
      try {
        const qs = new URLSearchParams()
        qs.set('source', 'media')
        qs.set('offer_tab', '')
        qs.set('parent_key', expandedMediaHouseKey)
        const out = await apiFetch<{ group: ApiPublisherGroup | null }>(
          `/api/dashboard/publisher-crm/detail?${qs.toString()}`,
        )
        if (cancelled || !out.group) return
        const ui = mapApiPublisherGroup(out.group)
        setCompanyGroups((prev) =>
          prev.map((x) => (x.key === expandedMediaHouseKey ? { ...x, brands: ui.brands ?? [] } : x)),
        )
      } catch {
        /* keep list row */
      } finally {
        if (!cancelled) setMediaBrandsLoadingKey(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [crmSource, expandedMediaHouseKey, listEpoch])

  async function syncIndependent() {
    setBusy('sync-ind')
    try {
      await apiFetch('/api/sync/notion/independent', { method: 'POST', silent: true })
      setListPage(1)
      setListEpoch((e) => e + 1)
    } finally {
      setBusy('')
    }
  }

  async function syncMedia() {
    setBusy('sync-media')
    try {
      await apiFetch('/api/sync/notion/media-houses', { method: 'POST', silent: true })
      setListPage(1)
      setListEpoch((e) => e + 1)
    } finally {
      setBusy('')
    }
  }

  async function pickIndependent(g: CompanyGroup) {
    if (!g.company_id) return
    const nextSel = {
      source: 'independent' as const,
      companyId: g.company_id,
      contactId: null,
      advisorCompanyId: null,
      sessionId: null,
    }
    setSelection(nextSel)
    nav(`/intelligence?${toWorkspaceSearchString(nextSel)}`)
  }

  async function pickMediaChild(childId: number) {
    setBusy(`open-${childId}`)
    try {
      const r = await apiFetch<{
        session_id: number
        company_id: number
        contact_id: number
        advisor_company_id: number
      }>(`/api/advisor/companies/${childId}/session`, { method: 'POST', silent: true })
      const nextSel = {
        source: 'media' as const,
        companyId: r.company_id,
        contactId: r.contact_id,
        sessionId: r.session_id,
        advisorCompanyId: r.advisor_company_id,
      }
      setSelection(nextSel)
      nav(`/intelligence?${toWorkspaceSearchString(nextSel)}`)
    } finally {
      setBusy('')
    }
  }

  function openRetagForIndependent(g: CompanyGroup) {
    if (!g.company_id) return
    setRetagSourceType('independent')
    setRetagSourceId(g.company_id)
    setRetagLabel(g.displayTitle || '')
    setRetagTargetType('media_house')
    setRetagParentName('')
    setRetagReason('')
    setRetagOpen(true)
  }

  function openRetagForMediaBrand(advisorCompanyId: number, domain: string) {
    setRetagSourceType('media_house')
    setRetagSourceId(advisorCompanyId)
    setRetagLabel(domain || '')
    setRetagTargetType('independent')
    setRetagParentName('')
    setRetagReason('')
    setRetagOpen(true)
  }

  async function submitRetag() {
    if (!retagSourceId) return
    if (retagTargetType === 'media_house' && !retagParentName.trim()) return
    setBusy('retag')
    setErr('')
    try {
      await apiFetch('/api/companies/reclassify', {
        method: 'POST',
        json: {
          source_type: retagSourceType,
          source_id: retagSourceId,
          target_type: retagTargetType,
          parent_name: retagTargetType === 'media_house' ? retagParentName.trim() : null,
          reason: retagReason.trim() || null,
        },
      })
      setRetagOpen(false)
      setListPage(1)
      setListEpoch((e) => e + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Retag failed')
    } finally {
      setBusy('')
    }
  }

  async function submitCreateMediaHouse() {
    const parent = addMediaParent.trim()
    const domain = addMediaDomain.trim()
    if (!parent || !domain) return
    setBusy('create-media')
    setErr('')
    try {
      await apiFetch('/api/advisor/companies', {
        method: 'POST',
        json: { parent_name: parent, domain },
      })
      setAddMediaOpen(false)
      setAddMediaParent('')
      setAddMediaDomain('')
      setListPage(1)
      setListEpoch((e) => e + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Create media house failed')
    } finally {
      setBusy('')
    }
  }

  const totalPages = Math.max(1, Math.ceil(listTotal / pageSize) || 1)
  const qLower = dq.trim().toLowerCase()
  const visibleGroups = companyGroups

  return (
    <div className="page-wide">
      <h1>Companies</h1>
      <div className="toolbar companies-toolbar">
        <div className="crm-source companies-source">
          <label className="muted small">Source</label>
          <div className="crm-seg">
            <button
              type="button"
              className={crmSource === 'independent' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => {
                if (crmSource !== 'independent') setCrmSource('independent')
                setSelection({
                  source: 'independent',
                  companyId: null,
                  contactId: null,
                  advisorCompanyId: null,
                  sessionId: null,
                })
              }}
              disabled={!!busy || loading}
            >
              Independent
            </button>
            <button
              type="button"
              className={crmSource === 'media' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => {
                if (crmSource !== 'media') setCrmSource('media')
                setSelection({
                  source: 'media',
                  companyId: null,
                  contactId: null,
                  advisorCompanyId: null,
                  sessionId: null,
                })
              }}
              disabled={!!busy || loading}
            >
              Media Houses
            </button>
          </div>
        </div>
        <input
          className="search companies-search"
          placeholder={crmSource === 'media' ? 'Search parent / brands / SPOCs…' : 'Search domain / SPOCs…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {crmSource === 'independent' ? (
          <button type="button" className="secondary" disabled={!!busy} onClick={syncIndependent}>
            {busy === 'sync-ind' ? '…' : 'Sync Notion'}
          </button>
        ) : (
          <>
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() => {
                setAddMediaOpen(true)
                setAddMediaParent('')
                setAddMediaDomain('')
              }}
            >
              + Add Media House
            </button>
            <button type="button" className="secondary" disabled={!!busy} onClick={syncMedia}>
              {busy === 'sync-media' ? '…' : 'Sync Media Houses'}
            </button>
          </>
        )}
      </div>
      {err && <p className="error">{err}</p>}
      {loading ? (
        <p className="muted">Loading…</p>
      ) : crmSource === 'independent' ? (
        <>
          <p className="muted small companies-row-hint">Click a row to open Intelligence for that company.</p>
          <div className="lh2-table-wrap crm-table-wrap">
            <table className="lh2-data-table crm-table">
              <thead>
                <tr>
                  <CrmResearchTh />
                  <th>Website</th>
                  <th>SPOC name</th>
                  <th>Status</th>
                  <th>Next step</th>
                  <th>Traffic</th>
                  <th>Syndication</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleGroups.length === 0 && (
                  <tr>
                    <td className="muted" colSpan={8}>
                      No companies match this search{qLower ? '.' : '.'}
                    </td>
                  </tr>
                )}
                {visibleGroups.map((g) => {
                  const spocs = (g.contacts_summary || []).filter((c) => c.kind === 'spoc')
                  const spocSummary = formatSpocSummary(spocs)
                  return (
                    <tr
                      key={g.key}
                      className="crm-row"
                      tabIndex={0}
                      role="button"
                      onClick={() => pickIndependent(g)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          pickIndependent(g)
                        }
                      }}
                      aria-label={`Open ${g.displayTitle} in Intelligence`}
                    >
                      <td className="crm-research-col">
                        <CrmResearchDot researched={g.hasResearch} />
                      </td>
                      <td>
                        <strong>{g.displayTitle}</strong>
                      </td>
                      <td>
                        <div>{spocSummary}</div>
                      </td>
                      <td>
                        <span className="crm-status-pill">{g.status_label}</span>
                      </td>
                      <td>
                        <div>{g.next_step_date}</div>
                        {g.due_today ? <div className="crm-due-today">Due</div> : null}
                      </td>
                      <td>{fmtVisits(g.monthly_visits)}</td>
                      <td>
                        <SyndicationBadges syndication={g.syndication} />
                      </td>
                      <td>
                        <button
                          type="button"
                          className="secondary sm"
                          disabled={!!busy}
                          onClick={(e) => {
                            e.stopPropagation()
                            openRetagForIndependent(g)
                          }}
                        >
                          Retag
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <p className="muted small companies-row-hint">Click a child row to open Intelligence (media property).</p>
          <div className="lh2-table-wrap crm-table-wrap">
            <table className="lh2-data-table crm-table">
              <thead>
                <tr>
                  <th>Media House</th>
                  <th>Brands</th>
                  <th>Contacted</th>
                  <th>CEO / Decision Maker</th>
                  <th>Total Traffic</th>
                  <th>Syndication</th>
                </tr>
              </thead>
              <tbody>
                {visibleGroups.length === 0 && (
                  <tr>
                    <td className="muted" colSpan={6}>
                      No media-house companies match this search.
                    </td>
                  </tr>
                )}
                {visibleGroups.map((g) => {
                  const expanded = expandedMediaHouseKey === g.key
                  const brands = g.brands || []
                  const loadingBrands = mediaBrandsLoadingKey === g.key
                  const brandsToShow = !qLower
                    ? brands
                    : brands.filter((b) => {
                        if ((b.company_domain || '').toLowerCase().includes(qLower)) return true
                        const spocs = (b.contacts_summary || []).filter((c) => c.kind === 'spoc')
                        return spocs.some(
                          (s) =>
                            (s.name || '').toLowerCase().includes(qLower) ||
                            (s.email || '').toLowerCase().includes(qLower),
                        )
                      })

                  const contactedCount = brandsToShow.filter((b) => b.status !== statusBuckets.notContacted).length
                  const ceoContact = (g.contacts_summary || []).find((c) => c.kind === 'contact')

                  return (
                    <Fragment key={g.key}>
                      <tr
                        className={expanded ? 'crm-row active' : 'crm-row'}
                        tabIndex={0}
                        role="button"
                        onClick={() => setExpandedMediaHouseKey(expanded ? null : g.key)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            setExpandedMediaHouseKey(expanded ? null : g.key)
                          }
                        }}
                        aria-label={`Expand media house ${g.displayTitle}`}
                      >
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span
                              style={{
                                fontSize: 11,
                                color: 'var(--muted)',
                                display: 'inline-block',
                                transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
                                transition: 'transform .2s',
                              }}
                            >
                              ▶
                            </span>
                            <strong>{g.displayTitle}</strong>
                          </div>
                        </td>
                        <td>{g.brandCount ?? brands.length}</td>
                        <td>
                          <span className="muted small">{g.contactedBrandCount ?? contactedCount}</span>
                        </td>
                        <td>
                          <div>{ceoContact?.name || g.spoc_name}</div>
                        </td>
                        <td>{fmtVisits(g.monthly_visits)}</td>
                        <td>
                          <SyndicationBadges syndication={g.syndication} />
                        </td>
                      </tr>

                      {expanded && (
                        <tr>
                          <td colSpan={6} style={{ padding: 0 }}>
                            <div className="crm-media-children-wrap">
                              {loadingBrands ? (
                                <p className="muted small" style={{ padding: 12 }}>
                                  Loading brands…
                                </p>
                              ) : (
                                <table className="lh2-data-table crm-table crm-child-table">
                                  <thead>
                                    <tr>
                                      <CrmResearchTh />
                                      <th>Website</th>
                                      <th>SPOC name</th>
                                      <th>Status</th>
                                      <th>Next step</th>
                                      <th>Traffic</th>
                                      <th>Syndication</th>
                                      <th>Actions</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {brandsToShow.length === 0 && (
                                      <tr>
                                        <td className="muted" colSpan={8}>
                                          No matching brands in this media house.
                                        </td>
                                      </tr>
                                    )}
                                    {brandsToShow.map((b) => {
                                      const spocs = (b.contacts_summary || []).filter((c) => c.kind === 'spoc')
                                      const spocSummary = formatSpocSummary(spocs)
                                      const canOpen = b.advisor_company_id != null
                                      return (
                                        <tr
                                          key={String(b.advisor_company_id || '') + b.company_domain}
                                          className={canOpen ? 'crm-row' : 'crm-row disabled'}
                                          tabIndex={0}
                                          role="button"
                                          onClick={() => {
                                            if (busy) return
                                            if (!canOpen) return
                                            pickMediaChild(b.advisor_company_id as number)
                                          }}
                                          onKeyDown={(e) => {
                                            if (!canOpen) return
                                            if (busy) return
                                            if (e.key === 'Enter' || e.key === ' ') {
                                              e.preventDefault()
                                              pickMediaChild(b.advisor_company_id as number)
                                            }
                                          }}
                                          aria-label={`Open ${b.company_domain} in Intelligence`}
                                        >
                                          <td className="crm-research-col">
                                            <CrmResearchDot researched={b.hasResearch} />
                                          </td>
                                          <td>
                                            <strong>{b.company_domain}</strong>
                                          </td>
                                          <td>
                                            <div>{spocSummary}</div>
                                          </td>
                                          <td>
                                            <span className="crm-status-pill">{b.status_label}</span>
                                          </td>
                                          <td>
                                            <div>{b.next_step_date}</div>
                                            {b.due_today ? <div className="crm-due-today">Due</div> : null}
                                          </td>
                                          <td>{fmtVisits(b.monthly_visits)}</td>
                                          <td>
                                            <SyndicationBadges syndication={b.syndication} />
                                          </td>
                                          <td>
                                            {canOpen ? (
                                              <button
                                                type="button"
                                                className="secondary sm"
                                                disabled={!!busy}
                                                onClick={(e) => {
                                                  e.stopPropagation()
                                                  openRetagForMediaBrand(
                                                    b.advisor_company_id as number,
                                                    b.company_domain,
                                                  )
                                                }}
                                              >
                                                Retag
                                              </button>
                                            ) : null}
                                          </td>
                                        </tr>
                                      )
                                    })}
                                  </tbody>
                                </table>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {retagOpen ? (
        <div
          className="crm-add-publisher-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setRetagOpen(false)
          }}
        >
          <div className="panel crm-add-publisher-modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Retag company</h2>
            <p className="muted small">Update classification for {retagLabel || 'this company'}.</p>
            <div className="crm-add-publisher-fields">
              <label className="crm-add-field">
                <span>Target type</span>
                <select
                  value={retagTargetType}
                  onChange={(e) => setRetagTargetType(e.target.value as 'independent' | 'media_house')}
                >
                  <option value="independent">Independent</option>
                  <option value="media_house">Media House</option>
                </select>
              </label>
              {retagTargetType === 'media_house' ? (
                <label className="crm-add-field">
                  <span>Media house (parent)</span>
                  <input
                    type="text"
                    value={retagParentName}
                    onChange={(e) => setRetagParentName(e.target.value)}
                    placeholder="e.g. Future plc"
                    list="companies-retag-parent-datalist"
                    autoComplete="off"
                  />
                  <datalist id="companies-retag-parent-datalist">
                    {filteredRetagParentSuggestions.map((p) => (
                      <option key={p} value={p} />
                    ))}
                  </datalist>
                  {retagParentName.trim() && !hasExactRetagParentMatch ? (
                    <span className="muted small">No exact match. Save retag to create this media house parent.</span>
                  ) : null}
                </label>
              ) : null}
              <label className="crm-add-field">
                <span>Reason (optional)</span>
                <input
                  type="text"
                  value={retagReason}
                  onChange={(e) => setRetagReason(e.target.value)}
                  autoComplete="off"
                />
              </label>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <button type="button" className="secondary sm" onClick={() => setRetagOpen(false)} disabled={!!busy}>
                Cancel
              </button>
              <button
                type="button"
                className="primary sm"
                onClick={() => void submitRetag()}
                disabled={!!busy || !retagSourceId || (retagTargetType === 'media_house' && !retagParentName.trim())}
              >
                {busy === 'retag' ? 'Saving…' : 'Save retag'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {addMediaOpen ? (
        <div
          className="crm-add-publisher-overlay"
          role="dialog"
          aria-modal="true"
          onClick={(e) => {
            if (e.target === e.currentTarget) setAddMediaOpen(false)
          }}
        >
          <div className="panel crm-add-publisher-modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ marginTop: 0 }}>Add media house brand</h2>
            <div className="crm-add-publisher-fields">
              <label className="crm-add-field">
                <span>Media house (parent)</span>
                <input
                  type="text"
                  value={addMediaParent}
                  onChange={(e) => setAddMediaParent(e.target.value)}
                  list="companies-media-parent-datalist"
                  autoComplete="off"
                />
                <datalist id="companies-media-parent-datalist">
                  {(mediaParentSuggestions || []).slice(0, 25).map((p) => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </label>
              <label className="crm-add-field">
                <span>Brand domain</span>
                <input
                  type="text"
                  value={addMediaDomain}
                  onChange={(e) => setAddMediaDomain(e.target.value)}
                  placeholder="e.g. 9to5mac.com"
                  autoComplete="off"
                />
              </label>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <button type="button" className="secondary sm" onClick={() => setAddMediaOpen(false)} disabled={!!busy}>
                Cancel
              </button>
              <button
                type="button"
                className="primary sm"
                onClick={() => void submitCreateMediaHouse()}
                disabled={!!busy || !addMediaParent.trim() || !addMediaDomain.trim()}
              >
                {busy === 'create-media' ? 'Creating…' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {!loading && (
        <div className="crm-pagination companies-pagination" style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <span className="muted small">
            {listTotal.toLocaleString()} {crmSource === 'media' ? 'media houses' : 'companies'}
            {listTotal > 0 ? ` · page ${listPage} / ${totalPages}` : ''}
          </span>
          <label className="muted small" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            Per page
            <select
              value={String(pageSize)}
              onChange={(e) => {
                setPageSize(clampPageSize(Number(e.target.value)))
                setListPage(1)
              }}
              disabled={!!busy}
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="secondary sm"
            disabled={listPage <= 1 || !!busy}
            onClick={() => setListPage((p) => Math.max(1, p - 1))}
          >
            Previous
          </button>
          <button
            type="button"
            className="secondary sm"
            disabled={listPage >= totalPages || !!busy || listTotal === 0}
            onClick={() => setListPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
