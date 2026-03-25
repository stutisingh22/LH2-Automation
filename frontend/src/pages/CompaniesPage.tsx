import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import {
  mapApiPublisherGroup,
  type ApiPublisherGroup,
  type UiPublisherGroup as CompanyGroup,
} from '../api/publisherCrm'
import { stripWorkspaceFromSearchParams, toWorkspaceSearchString } from '../lib/workspaceUrl'
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
  const companyGroupsRef = useRef(companyGroups)
  companyGroupsRef.current = companyGroups

  const prevFilterKey = useRef<string | null>(null)

  const statusOptions = meta?.contact_statuses || []

  const statusBuckets = useMemo(() => {
    const notContacted: string =
      statusOptions.find((s) => s === 'not_contacted') || statusOptions[0] || 'not_contacted'
    return { notContacted }
  }, [statusOptions])

  useLayoutEffect(() => {
    const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1)
    const limit = clampPageSize(parseInt(searchParams.get('limit') || String(DEFAULT_PAGE_SIZE), 10) || DEFAULT_PAGE_SIZE)
    const source: 'independent' | 'media' = searchParams.get('source') === 'media' ? 'media' : 'independent'
    const qParam = searchParams.get('q') ?? ''
    setListPage(page)
    setPageSize(limit)
    setCrmSource(source)
    setQ(qParam)
  }, [searchParams])

  useEffect(() => {
    const fk = `${crmSource}|${dq.trim()}`
    if (prevFilterKey.current !== null && prevFilterKey.current !== fk) {
      setListPage(1)
    }
    prevFilterKey.current = fk
  }, [crmSource, dq])

  useEffect(() => {
    const sourceInUrl = searchParams.get('source')
    if (sourceInUrl && sourceInUrl !== crmSource) return
    const p = new URLSearchParams()
    p.set('source', crmSource)
    p.set('page', String(listPage))
    p.set('limit', String(pageSize))
    if (dq.trim()) p.set('q', dq.trim())
    for (const [k, v] of new URLSearchParams(searchParams).entries()) {
      if (k.startsWith('sel_')) p.set(k, v)
    }
    if (companiesUrlParamsEqual(p, new URLSearchParams(searchParams))) return
    setSearchParams(p, { replace: true })
  }, [crmSource, listPage, pageSize, dq, searchParams, setSearchParams])

  useEffect(() => {
    if (searchParams.has('source')) return
    setCrmSource(selection.source === 'media' ? 'media' : 'independent')
  }, [selection.source, searchParams])

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

  const totalPages = Math.max(1, Math.ceil(listTotal / pageSize) || 1)
  const qLower = dq.trim().toLowerCase()
  const visibleGroups = companyGroups

  return (
    <div className="page-wide">
      <h1>Companies</h1>
      <div className="toolbar" style={{ flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
        <div className="crm-source" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label className="muted small">Source</label>
          <div className="crm-seg">
            <button
              type="button"
              className={crmSource === 'independent' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => {
                setCrmSource('independent')
                setSelection({
                  source: 'independent',
                  companyId: null,
                  contactId: null,
                  advisorCompanyId: null,
                  sessionId: null,
                })
                setSearchParams(
                  (prev) => {
                    const p = new URLSearchParams(prev)
                    stripWorkspaceFromSearchParams(p)
                    return p
                  },
                  { replace: true },
                )
              }}
              disabled={!!busy || loading}
            >
              Independent
            </button>
            <button
              type="button"
              className={crmSource === 'media' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => {
                setCrmSource('media')
                setSelection({
                  source: 'media',
                  companyId: null,
                  contactId: null,
                  advisorCompanyId: null,
                  sessionId: null,
                })
                setSearchParams(
                  (prev) => {
                    const p = new URLSearchParams(prev)
                    stripWorkspaceFromSearchParams(p)
                    return p
                  },
                  { replace: true },
                )
              }}
              disabled={!!busy || loading}
            >
              Media Houses
            </button>
          </div>
        </div>
        <input
          className="search"
          style={{ flex: '1 1 200px', minWidth: 180 }}
          placeholder={crmSource === 'media' ? 'Search parent / brands / SPOCs…' : 'Search domain / SPOCs…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {crmSource === 'independent' ? (
          <button type="button" className="secondary" disabled={!!busy} onClick={syncIndependent}>
            {busy === 'sync-ind' ? '…' : 'Sync Notion'}
          </button>
        ) : (
          <button type="button" className="secondary" disabled={!!busy} onClick={syncMedia}>
            {busy === 'sync-media' ? '…' : 'Sync Media Houses'}
          </button>
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
                </tr>
              </thead>
              <tbody>
                {visibleGroups.length === 0 && (
                  <tr>
                    <td className="muted" colSpan={7}>
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
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {brandsToShow.length === 0 && (
                                      <tr>
                                        <td className="muted" colSpan={7}>
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
