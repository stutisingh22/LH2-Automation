import { Fragment, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useDebounced } from '../hooks/useDebounced'

type ContactSummary = {
  kind: 'contact' | 'spoc'
  name: string
  role: string
  email: string
  linkedin_url: string
}

type PublisherCrmRow = {
  company_id: number
  contact_id: number
  advisor_company_id: number | null
  parent_name?: string
  company_domain: string
  status: string
  status_label: string
  next_step_date: string
  due_today: boolean
  monthly_visits: number | null
  syndication: string[]
  contacts_summary: ContactSummary[]
  planned_preview?: unknown[]
  advisor_company_ids?: number[]
  contact_ids?: number[]
}

type BrandSummary = {
  company_domain: string
  status: string
  status_label: string
  next_step_date: string
  due_today: boolean
  monthly_visits: number | null
  syndication: string[]
  spoc_name: string
  contacts_summary: ContactSummary[]
  advisor_company_id: number | null
}

type CompanyGroup = {
  key: string
  source: 'independent' | 'media'
  displayTitle: string
  company_id?: number
  parent_name?: string
  status: string
  status_label: string
  due_today: boolean
  next_step_date: string
  monthly_visits: number | null
  syndication: string[]
  spoc_name: string
  contacts_summary: ContactSummary[]
  brands?: BrandSummary[]
}

function uniqueBy<T>(items: T[], keyFn: (it: T) => string) {
  const seen = new Set<string>()
  const out: T[] = []
  for (const it of items) {
    const k = keyFn(it)
    if (seen.has(k)) continue
    seen.add(k)
    out.push(it)
  }
  return out
}

function fmtVisits(v: number | null | undefined) {
  const n = typeof v === 'number' ? v : Number(v || 0)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace('.0', '')}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1).replace('.0', '')}K`
  return `${Math.round(n)}`
}

function parseDisplayDate(s: string | undefined) {
  if (!s) return null
  const v = String(s).trim()
  if (!v || v === '—') return null
  const ms = Date.parse(v)
  return Number.isFinite(ms) ? new Date(ms) : null
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
  const [rows, setRows] = useState<PublisherCrmRow[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [expandedMediaHouseKey, setExpandedMediaHouseKey] = useState<string | null>(null)

  const src = selection.source

  const COMP_CACHE_TTL_MS = 60_000
  const cacheKeyFor = (source: typeof src) => `lh2_companies_crm_cache_v1:${source}`

  const readCache = (key: string): { ts: number; rows: PublisherCrmRow[] } | null => {
    try {
      const raw = sessionStorage.getItem(key)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { ts?: number; rows?: PublisherCrmRow[] }
      if (!parsed || typeof parsed.ts !== 'number' || !Array.isArray(parsed.rows)) return null
      if (Date.now() - parsed.ts > COMP_CACHE_TTL_MS) return null
      return { ts: parsed.ts, rows: parsed.rows }
    } catch {
      return null
    }
  }

  const writeCache = (key: string, rows: PublisherCrmRow[]) => {
    try {
      sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), rows }))
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
    const key = cacheKeyFor(src)

    if (!force) {
      const cached = readCache(key)
      if (cached) {
        setRows(cached.rows || [])
        setExpandedMediaHouseKey(null)
        return
      }
    }

    const qs = new URLSearchParams()
    qs.set('source', src)
    qs.set('offer_tab', '')
    const out = await apiFetch<{ kpis: Record<string, number>; rows: PublisherCrmRow[] }>(`/api/dashboard/publisher-crm?${qs.toString()}`)
    setRows(out.rows || [])
    writeCache(key, out.rows || [])
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  async function syncIndependent() {
    setBusy('sync-ind')
    try {
      await apiFetch('/api/sync/notion/independent', { method: 'POST' })
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
      await apiFetch('/api/sync/notion/media-houses', { method: 'POST' })
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
      }>(`/api/advisor/companies/${childId}/session`, { method: 'POST' })
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

  const companyGroups = useMemo(() => {
    const groupMap = new Map<string, PublisherCrmRow[]>()

    const keyFor = (r: PublisherCrmRow) => {
      if (src === 'media') return `parent:${r.parent_name || '(No parent)'}`
      return `company:${r.company_id}`
    }

    for (const r of rows) {
      const k = keyFor(r)
      if (!groupMap.has(k)) groupMap.set(k, [])
      groupMap.get(k)!.push(r)
    }

    const statusRank = (st: string) => {
      const idx = statusOptions.indexOf(st)
      return idx >= 0 ? idx : -1
    }

    const groups: CompanyGroup[] = []

    for (const [key, arr] of groupMap.entries()) {
      const displayTitle = src === 'media' ? arr[0].parent_name || '(No parent)' : arr[0].company_domain

      const dedupedContacts = uniqueBy(
        arr.flatMap((r) => r.contacts_summary || []),
        (c) => `${c.kind}|${c.email}|${c.linkedin_url}|${c.name}|${c.role}`,
      )

      const statusBest = arr.slice().sort((a, b) => statusRank(b.status) - statusRank(a.status))[0] || arr[0]

      const due_today = arr.some((x) => !!x.due_today)

      const dated = arr
        .map((x) => ({ x, d: parseDisplayDate(x.next_step_date) }))
        .filter((v) => v.d)
        .sort((a, b) => (a.d!.getTime() - b.d!.getTime()))

      const next_step_date = dated[0]?.x.next_step_date || arr.find((x) => x.due_today)?.next_step_date || '—'

      const syndication = uniqueBy(arr.flatMap((x) => x.syndication || []), (s) => s)

      const monthly_visits =
        src === 'media'
          ? arr.reduce((sum, x) => sum + (typeof x.monthly_visits === 'number' ? x.monthly_visits : 0), 0) || null
          : arr[0].monthly_visits ?? null

      const primarySpoc = dedupedContacts.find((c) => c.kind === 'spoc') || dedupedContacts[0] || null

      const brands =
        src === 'media'
          ? (() => {
              const brandMap = new Map<string, PublisherCrmRow[]>()
              for (const r of arr) {
                const bk = r.company_domain
                if (!brandMap.has(bk)) brandMap.set(bk, [])
                brandMap.get(bk)!.push(r)
              }

              const out: BrandSummary[] = []
              for (const [bk, brArr] of brandMap.entries()) {
                const best = brArr.slice().sort((a, b) => statusRank(b.status) - statusRank(a.status))[0] || brArr[0]
                const brandContacts = uniqueBy(
                  brArr.flatMap((r) => r.contacts_summary || []),
                  (c) => `${c.kind}|${c.email}|${c.linkedin_url}|${c.name}|${c.role}`,
                )

                const brandPrimarySpoc = brandContacts.find((c) => c.kind === 'spoc') || brandContacts[0] || null

                const brandDue = brArr.some((x) => !!x.due_today)
                const brandDated = brArr
                  .map((x) => ({ x, d: parseDisplayDate(x.next_step_date) }))
                  .filter((v) => v.d)
                  .sort((a, b) => (a.d!.getTime() - b.d!.getTime()))
                const brandNext = brandDated[0]?.x.next_step_date || brArr.find((x) => x.due_today)?.next_step_date || '—'

                out.push({
                  company_domain: bk,
                  status: best.status,
                  status_label: best.status_label,
                  next_step_date: brandNext,
                  due_today: brandDue,
                  monthly_visits: best.monthly_visits,
                  syndication: uniqueBy(brArr.flatMap((x) => x.syndication || []), (s) => s),
                  spoc_name: brandPrimarySpoc?.name || bk,
                  contacts_summary: brandContacts,
                  advisor_company_id: best.advisor_company_id,
                })
              }

              out.sort((a, b) => (a.due_today === b.due_today ? 0 : a.due_today ? -1 : 1))
              return out
            })()
          : undefined

      groups.push({
        key,
        source: src,
        displayTitle,
        company_id: src === 'independent' ? arr[0].company_id : undefined,
        parent_name: src === 'media' ? arr[0].parent_name || '(No parent)' : undefined,
        status: statusBest.status,
        status_label: statusBest.status_label,
        due_today,
        next_step_date,
        monthly_visits,
        syndication,
        spoc_name: primarySpoc?.name || displayTitle,
        contacts_summary: dedupedContacts,
        brands,
      })
    }

    groups.sort((a, b) => {
      if (a.due_today !== b.due_today) return a.due_today ? -1 : 1
      const ad = parseDisplayDate(a.next_step_date)
      const bd = parseDisplayDate(b.next_step_date)
      if (ad && bd && ad.getTime() !== bd.getTime()) return ad.getTime() - bd.getTime()
      return a.displayTitle.localeCompare(b.displayTitle)
    })

    return groups
  }, [rows, src, statusOptions])

  const visibleGroups = useMemo(() => {
    const q = dq.trim().toLowerCase()
    if (!q) return companyGroups

    const matchesIndependent = (g: CompanyGroup) => {
      const title = (g.displayTitle || '').toLowerCase()
      if (title.includes(q)) return true
      const spocs = (g.contacts_summary || []).filter((c) => c.kind === 'spoc')
      return spocs.some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
    }

    const matchesMedia = (g: CompanyGroup) => {
      const title = (g.displayTitle || '').toLowerCase()
      if (title.includes(q)) return true
      return (g.brands || []).some((b) => {
        if ((b.company_domain || '').toLowerCase().includes(q)) return true
        const spocs = (b.contacts_summary || []).filter((c) => c.kind === 'spoc')
        return spocs.some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
      })
    }

    return companyGroups.filter((g) => (src === 'media' ? matchesMedia(g) : matchesIndependent(g)))
  }, [companyGroups, dq, src])

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
