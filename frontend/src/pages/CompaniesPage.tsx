import { Fragment, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import {
  mapApiPublisherGroup,
  type ApiPublisherGroup,
  type UiPublisherGroup as CompanyGroup,
} from '../api/publisherCrm'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useDebounced } from '../hooks/useDebounced'

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

export default function CompaniesPage() {
  const { selection, setSelection } = useSelection()
  const meta = useMeta()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 280)
  const [companyGroups, setCompanyGroups] = useState<CompanyGroup[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [expandedMediaHouseKey, setExpandedMediaHouseKey] = useState<string | null>(null)

  const src = selection.source

  const COMP_CACHE_TTL_MS = 60_000
  const cacheKeyFor = (source: typeof src) => `lh2_companies_crm_cache_v1:${source}`

  const readCache = (key: string): { ts: number; groups: CompanyGroup[] } | null => {
    try {
      const raw = sessionStorage.getItem(key)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { ts?: number; groups?: CompanyGroup[] }
      if (!parsed || typeof parsed.ts !== 'number' || !Array.isArray(parsed.groups)) return null
      if (Date.now() - parsed.ts > COMP_CACHE_TTL_MS) return null
      return { ts: parsed.ts, groups: parsed.groups }
    } catch {
      return null
    }
  }

  const writeCache = (key: string, groups: CompanyGroup[]) => {
    try {
      sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), groups }))
    } catch {
      /* ignore */
    }
  }

  const statusOptions = meta?.contact_statuses || []

  const statusBuckets = useMemo(() => {
    const notContacted: string = statusOptions.find((s) => s === 'not_contacted') || statusOptions[0] || 'not_contacted'
    return { notContacted }
  }, [statusOptions])

  const loadCompanies = async (opts?: { force?: boolean }) => {
    const force = !!opts?.force
    const key = `${cacheKeyFor(src)}:${dq.trim().toLowerCase()}`

    if (!force) {
      const cached = readCache(key)
      if (cached) {
        setCompanyGroups(cached.groups || [])
        setExpandedMediaHouseKey(null)
        return
      }
    }

    const qs = new URLSearchParams()
    qs.set('source', src)
    qs.set('offer_tab', '')
    qs.set('page', '1')
    qs.set('page_size', '3000')
    qs.set('kpi', 'total')
    if (dq.trim()) qs.set('search', dq.trim())
    qs.set('due_dates', '')
    const out = await apiFetch<{
      kpis: Record<string, number>
      groups: ApiPublisherGroup[]
      total: number
      page: number
      page_size: number
    }>(`/api/dashboard/publisher-crm?${qs.toString()}`)
    const mapped = (out.groups || []).map(mapApiPublisherGroup)
    setCompanyGroups(mapped)
    writeCache(key, mapped)
    setExpandedMediaHouseKey(null)
  }

  useEffect(() => {
    let c = true
    setLoading(true)
    setErr('')

    ;(async () => {
      try {
        await loadCompanies()
      } catch (e) {
        if (!c) return
        setErr(e instanceof Error ? e.message : 'Load failed')
      } finally {
        if (c) setLoading(false)
      }
    })()

    return () => {
      c = false
    }
  }, [src, dq])

  async function syncIndependent() {
    setBusy('sync-ind')
    try {
      await apiFetch('/api/sync/notion/independent', { method: 'POST', silent: true })
    } finally {
      setBusy('')
    }
    try {
      await loadCompanies({ force: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Refresh failed')
    }
  }

  async function syncMedia() {
    setBusy('sync-media')
    try {
      await apiFetch('/api/sync/notion/media-houses', { method: 'POST', silent: true })
    } finally {
      setBusy('')
    }
    try {
      await loadCompanies({ force: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Refresh failed')
    }
  }

  async function pickIndependent(g: CompanyGroup) {
    if (!g.company_id) return
    setSelection({
      source: 'independent',
      companyId: g.company_id,
      contactId: null,
      advisorCompanyId: null,
      sessionId: null,
    })
    nav('/intelligence')
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
      setSelection({
        source: 'media',
        companyId: r.company_id,
        contactId: r.contact_id,
        sessionId: r.session_id,
        advisorCompanyId: r.advisor_company_id,
      })
      nav('/intelligence')
    } finally {
      setBusy('')
    }
  }

  const visibleGroups = companyGroups

  const qLower = dq.trim().toLowerCase()

  return (
    <div className="page-wide">
      <h1>Companies</h1>
      <div className="toolbar">
        <input
          className="search"
          placeholder={src === 'media' ? 'Search parent / brands / SPOCs…' : 'Search domain / SPOCs…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {src === 'independent' ? (
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
      ) : src === 'independent' ? (
        <>
          <p className="muted small companies-row-hint">Click a row to open Intelligence for that company.</p>
          <div className="lh2-table-wrap crm-table-wrap">
            <table className="lh2-data-table crm-table">
              <thead>
                <tr>
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
                    <td className="muted" colSpan={6}>
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
                  const brandsToShow = !qLower
                    ? brands
                    : brands.filter((b) => {
                        if ((b.company_domain || '').toLowerCase().includes(qLower)) return true
                        const spocs = (b.contacts_summary || []).filter((c) => c.kind === 'spoc')
                        return spocs.some(
                          (s) =>
                            (s.name || '').toLowerCase().includes(qLower) || (s.email || '').toLowerCase().includes(qLower),
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
                        <td>{brandsToShow.length}</td>
                        <td>
                          <span className="muted small">{contactedCount}</span>
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
                              <table className="lh2-data-table crm-table crm-child-table">
                                <thead>
                                  <tr>
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
                                      <td className="muted" colSpan={6}>
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
    </div>
  )
}
