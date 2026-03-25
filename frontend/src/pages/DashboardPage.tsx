import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useNavigate } from 'react-router-dom'

type PublisherCrmRow = {
  company_id: number
  contact_id: number
  advisor_company_id: number | null
  parent_name?: string
  source: 'independent' | 'media'
  company_domain: string
  spoc_name: string
  spoc_role: string
  spoc_email: string
  spoc_linkedin_url: string
  status: string
  status_label: string
  next_step_date: string
  due_today: boolean
  first_overdue_date_str?: string | null
  syndication: string[]
  monthly_visits: number | null
  planned_preview: {
    id: number
    sequence_step: number | null
    channel: string
    status: string
    scheduled_at: string | null
    description: string
  }[]
  contacts_summary?: {
    kind: 'contact' | 'spoc'
    name: string
    role: string
    email: string
    linkedin_url: string
  }[]
}

function fmtVisits(v: number | null | undefined) {
  const n = typeof v === 'number' ? v : Number(v || 0)
  if (!Number.isFinite(n) || n <= 0) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace('.0', '')}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1).replace('.0', '')}K`
  return `${Math.round(n)}`
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

type KpiKey = 'total' | 'in_campaign' | 'responded' | 'no_response' | 'won' | 'due'

type ContactSummary = NonNullable<PublisherCrmRow['contacts_summary']>[number]

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
  displayTitle: string // company_domain for independent, parent_name for media
  parent_name?: string
  company_id?: number

  status: string
  status_label: string
  due_today: boolean
  next_step_date: string
  monthly_visits: number | null // independent: company monthly_visits; media: sum across brands
  syndication: string[]

  // For compact table rendering
  spoc_name: string

  // Detail drawer data
  contacts_summary: ContactSummary[]
  brands?: BrandSummary[]

  // For patching status
  contact_ids: number[]
  advisor_company_ids: number[]
  planned_preview: PublisherCrmRow['planned_preview']
}

function uniqueBy<T>(items: T[], keyFn: (t: T) => string) {
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

function parseDisplayDate(s: string | undefined) {
  if (!s) return null
  const v = String(s).trim()
  if (!v || v === '—') return null
  const ms = Date.parse(v)
  return Number.isFinite(ms) ? new Date(ms) : null
}

function formatSpocSummary(spocs: { name: string }[]): string {
  // UI rule:
  // - 0 SPOCs => "-"
  // - 1 SPOC => "Jane Doe"
  // - n SPOCs => "Jane Doe +{n-1}"
  const cleaned = (spocs || [])
    .map((s) => (s?.name || '').trim())
    .filter((n) => n && n !== '—')
  if (cleaned.length === 0) return '-'
  if (cleaned.length === 1) return cleaned[0]
  return `${cleaned[0]} +${cleaned.length - 1}`
}

export default function DashboardPage() {
  const { selection, setSelection, clearWorkspace } = useSelection()
  const meta = useMeta()
  const navigate = useNavigate()

  const [crmSource, setCrmSource] = useState<'independent' | 'media'>(selection.source === 'media' ? 'media' : 'independent')
  const [offerTab, setOfferTab] = useState<'indication' | 'general_acq'>('indication')

  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [rows, setRows] = useState<PublisherCrmRow[]>([])

  const [activeKpiKey, setActiveKpiKey] = useState<KpiKey>('total')
  const [selectedGroup, setSelectedGroup] = useState<CompanyGroup | null>(null)
  const [expandedMediaHouseKey, setExpandedMediaHouseKey] = useState<string | null>(null)
  const [selectedMediaBrand, setSelectedMediaBrand] = useState<null | { parent: CompanyGroup; brand: BrandSummary }>(null)
  const [detailTab, setDetailTab] = useState<'overview' | 'campaign'>('overview')
  const [drawerYOffsetPx, setDrawerYOffsetPx] = useState(0)
  const crmGridWrapRef = useRef<HTMLDivElement | null>(null)

  const statusLabels = meta?.contact_status_labels_strip || {}
  const statusOptions = meta?.contact_statuses || []

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const

  const pad2 = (n: number) => String(n).padStart(2, '0')

  const formatUtcDisplayDate = (d: Date) => {
    const m = MONTHS[d.getUTCMonth()]
    const day = pad2(d.getUTCDate())
    const y = d.getUTCFullYear()
    return `${m} ${day}, ${y}`
  }

  const nowUtc = new Date()
  const todayUtc = new Date(Date.UTC(nowUtc.getUTCFullYear(), nowUtc.getUTCMonth(), nowUtc.getUTCDate()))
  const tomorrowUtc = new Date(todayUtc.getTime() + 86400000)

  const todayDisplay = formatUtcDisplayDate(todayUtc)
  const tomorrowDisplay = formatUtcDisplayDate(tomorrowUtc)

  const [dueFilterOpen, setDueFilterOpen] = useState(false)
  const [dueDisplayFilters, setDueDisplayFilters] = useState<string[]>([todayDisplay])
  const [customDueDate, setCustomDueDate] = useState<string>('') // yyyy-mm-dd
  const [includeCustomDue, setIncludeCustomDue] = useState(false)

  const [drawerHasResearch, setDrawerHasResearch] = useState<boolean | null>(null)
  const [drawerResearchBusy, setDrawerResearchBusy] = useState(false)

  const [crmSearchQuery, setCrmSearchQuery] = useState('')

  const CRM_CACHE_TTL_MS = 60_000

  const crmCacheKey = (source: typeof crmSource, tab: typeof offerTab) => `lh2_dashboard_crm_cache_v1:${source}:${tab}`

  const readCrmCache = (key: string): { ts: number; rows: PublisherCrmRow[] } | null => {
    try {
      const raw = sessionStorage.getItem(key)
      if (!raw) return null
      const parsed = JSON.parse(raw) as { ts?: number; rows?: PublisherCrmRow[] }
      if (!parsed || typeof parsed.ts !== 'number' || !Array.isArray(parsed.rows)) return null
      if (Date.now() - parsed.ts > CRM_CACHE_TTL_MS) return null
      return { ts: parsed.ts, rows: parsed.rows }
    } catch {
      return null
    }
  }

  const writeCrmCache = (key: string, rows: PublisherCrmRow[]) => {
    try {
      sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), rows }))
    } catch {
      /* ignore cache write failures */
    }
  }

  const customDueDisplay = (() => {
    if (!customDueDate) return ''
    const [y, m, d] = customDueDate.split('-').map((x) => Number(x))
    if (!y || !m || !d) return ''
    return formatUtcDisplayDate(new Date(Date.UTC(y, m - 1, d)))
  })()

  const prevCustomDueDisplayRef = useRef<string>('')

  useEffect(() => {
    // If user has the custom date included, keep dueDisplayFilters in sync
    if (!includeCustomDue) return
    const prev = prevCustomDueDisplayRef.current
    const next = customDueDisplay
    if (!next) return
    setDueDisplayFilters((arr) => {
      const base = arr.filter((x) => x !== prev)
      if (!base.includes(next)) base.push(next)
      return base
    })
    prevCustomDueDisplayRef.current = next
  }, [customDueDisplay, includeCustomDue])

  const scrollRestoreRef = useRef<{ el: HTMLElement; top: number } | null>(null)

  const captureScrollTop = () => {
    const el = document.querySelector('.frame-content') as HTMLElement | null
    if (!el) return
    scrollRestoreRef.current = { el, top: el.scrollTop }
  }

  useEffect(() => {
    const v = scrollRestoreRef.current
    if (!v) return
    // Restore after layout settles.
    requestAnimationFrame(() => {
      try {
        v.el.scrollTop = v.top
      } catch {
        /* ignore */
      } finally {
        scrollRestoreRef.current = null
      }
    })
  }, [selectedGroup])

  const fetchCrm = async (opts?: { force?: boolean }) => {
    const force = !!opts?.force

    const cacheKey = crmCacheKey(crmSource, offerTab)
    if (!force) {
      const cached = readCrmCache(cacheKey)
      if (cached) {
        setRows(cached.rows || [])
        setSelectedGroup(null)
        setExpandedMediaHouseKey(null)
        setSelectedMediaBrand(null)
        setDrawerHasResearch(null)
        setDrawerResearchBusy(false)
        return
      }
    }

    setBusy(true)
    setErr('')
    try {
      const q = new URLSearchParams()
      q.set('source', crmSource)
      q.set('offer_tab', offerTab)
      const out = await apiFetch<{ kpis: Record<string, number>; rows: PublisherCrmRow[] }>(
        `/api/dashboard/publisher-crm?${q.toString()}`,
      )
      setRows(out.rows || [])
      writeCrmCache(cacheKey, out.rows || [])
      setSelectedGroup(null)
      setExpandedMediaHouseKey(null)
      setSelectedMediaBrand(null)
      setDrawerHasResearch(null)
      setDrawerResearchBusy(false)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load dashboard')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    // Keep CRM source synced to the current workspace source only on first load / workspace changes.
    setCrmSource(selection.source === 'media' ? 'media' : 'independent')
  }, [selection.source])

  useEffect(() => {
    void fetchCrm()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crmSource, offerTab])

  // When the drawer opens, check whether any research has been saved already.
  useEffect(() => {
    let cancelled = false

    async function checkResearchExists() {
      const companyId = crmSource === 'independent' ? selectedGroup?.company_id : null
      const advisorCompanyId = crmSource === 'media' ? selectedMediaBrand?.brand.advisor_company_id : null

      if (!companyId && !advisorCompanyId) {
        setDrawerHasResearch(null)
        setDrawerResearchBusy(false)
        return
      }

      setDrawerResearchBusy(true)
      setDrawerHasResearch(null)
      try {
        const q = new URLSearchParams()
        if (companyId) q.set('company_id', String(companyId))
        if (advisorCompanyId != null) q.set('advisor_company_id', String(advisorCompanyId))
        const out = await apiFetch<{ has_research: boolean }>(`/api/intelligence/has-research?${q.toString()}`)
        if (!cancelled) setDrawerHasResearch(Boolean(out.has_research))
      } catch {
        if (!cancelled) setDrawerHasResearch(false)
      } finally {
        if (!cancelled) setDrawerResearchBusy(false)
      }
    }

    void checkResearchExists()
    return () => {
      cancelled = true
    }
  }, [crmSource, selectedGroup?.company_id, selectedMediaBrand?.brand.advisor_company_id])

  const selectedStatus =
    crmSource === 'media' ? selectedMediaBrand?.brand.status || '' : selectedGroup?.status || ''

  const patchGroupStatus = async (status: string) => {
    if (!selectedGroup) return
    setBusy(true)
    setErr('')
    try {
      const idsContact = uniqueBy(selectedGroup.contact_ids.filter((x) => x > 0), (x) => String(x))
      const idsAdvisor = uniqueBy(
        selectedGroup.advisor_company_ids.filter((x) => x !== null && Number.isFinite(Number(x))),
        (x) => String(x),
      ) as number[]

      if (crmSource === 'media') {
        if (!idsAdvisor.length) throw new Error('No Media House/child advisor IDs available to update.')
        await Promise.all(
          idsAdvisor.map((aid) =>
            apiFetch(`/api/pipeline/media-houses/companies/${aid}`, { method: 'PATCH', json: { status } }),
          ),
        )
      } else {
        if (!idsContact.length) throw new Error('No contacts available to update for this company yet.')
        await Promise.all(
          idsContact.map((cid) => apiFetch(`/api/pipeline/contacts/${cid}/status`, { method: 'PATCH', json: { status } })),
        )
      }
      await fetchCrm({ force: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Update failed')
    } finally {
      setBusy(false)
    }
  }

  const patchMediaBrandStatus = async (status: string) => {
    if (!selectedMediaBrand) return
    const aid = selectedMediaBrand.brand.advisor_company_id
    if (!aid) return
    setBusy(true)
    setErr('')
    try {
      await apiFetch(`/api/pipeline/media-houses/companies/${aid}`, { method: 'PATCH', json: { status } })
      await fetchCrm({ force: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Update failed')
    } finally {
      setBusy(false)
    }
  }

  const drawerVars = useMemo(
    () =>
      ({
        '--drawer-y': `${drawerYOffsetPx}px`,
      }) as React.CSSProperties & Record<string, string>,
    [drawerYOffsetPx],
  )

  const statusBuckets = useMemo(() => {
    const notContacted: string = statusOptions.find((s) => s === 'not_contacted') || statusOptions[0] || 'not_contacted'
    const inCampaign: string = statusOptions.find((s) => s === 'outreach_sent') || statusOptions[1] || 'outreach_sent'
    const responded: string[] = [
      statusOptions.find((s) => s === 'replied_positive') || 'replied_positive',
      statusOptions.find((s) => s === 'meeting_booked') || 'meeting_booked',
    ]
    const won: string = statusOptions.find((s) => s === 'partnership_active') || statusOptions[4] || 'partnership_active'

    return { notContacted, inCampaign, responded, won }
  }, [statusOptions])

  const companyGroups = useMemo(() => {
    const groupMap = new Map<string, PublisherCrmRow[]>()
    const keyFor = (r: PublisherCrmRow) => {
      if (crmSource === 'media') return `parent:${r.parent_name || '(No parent)'}`
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
      const source = crmSource
      const displayTitle = source === 'media' ? arr[0].parent_name || '(No parent)' : arr[0].company_domain

      const dedupedContacts = uniqueBy(
        arr.flatMap((r) => r.contacts_summary || []),
        (c) => `${c.kind}|${c.email}|${c.linkedin_url}|${c.name}`,
      )

      const statusBest = arr
        .slice()
        .sort((a, b) => statusRank(b.status) - statusRank(a.status))[0] || arr[0]

      const due_today = arr.some((x) => !!x.due_today)

      const dated = arr
        .map((x) => ({ x, d: parseDisplayDate(x.next_step_date) }))
        .filter((v) => v.d)
        .sort((a, b) => (a.d!.getTime() - b.d!.getTime()))

      const next_step_date = dated[0]?.x.next_step_date || arr.find((x) => x.due_today)?.next_step_date || '—'

      const syndication = uniqueBy(arr.flatMap((x) => x.syndication || []), (s) => s)

      const contact_ids = uniqueBy(arr.map((x) => x.contact_id).filter((x) => x > 0), (x) => String(x))

      const advisor_company_ids = uniqueBy(
        arr.map((x) => x.advisor_company_id).filter((x) => x !== null) as number[],
        (x) => String(x),
      )

      const planned_preview = uniqueBy(
        arr.flatMap((x) => x.planned_preview || []),
        (p) => String(p.id),
      )

      const monthly_visits =
        source === 'media'
          ? arr.reduce((sum, x) => sum + (typeof x.monthly_visits === 'number' ? x.monthly_visits : 0), 0) || null
          : arr[0].monthly_visits ?? null

      const primaryContact = dedupedContacts.find((c) => c.kind === 'spoc') || dedupedContacts.find(Boolean) || null

      const brands =
        source === 'media'
          ? (() => {
              const brandMap = new Map<string, PublisherCrmRow[]>()
              for (const r of arr) {
                const bk = r.company_domain
                if (!brandMap.has(bk)) brandMap.set(bk, [])
                brandMap.get(bk)!.push(r)
              }
              const out: BrandSummary[] = []
              for (const [bk, brArr] of brandMap.entries()) {
                const best = brArr
                  .slice()
                  .sort((a, b) => statusRank(b.status) - statusRank(a.status))[0] || brArr[0]
                const bdContacts = uniqueBy(
                  brArr.flatMap((r) => r.contacts_summary || []),
                  (c) => `${c.kind}|${c.email}|${c.linkedin_url}|${c.name}`,
                )
                out.push({
                  company_domain: bk,
                  status: best.status,
                  status_label: best.status_label,
                  next_step_date: best.next_step_date,
                  due_today: brArr.some((x) => !!x.due_today),
                  monthly_visits: best.monthly_visits,
                  syndication: uniqueBy(brArr.flatMap((x) => x.syndication || []), (s) => s),
                  spoc_name: best.spoc_name,
                  contacts_summary: bdContacts,
                  advisor_company_id: best.advisor_company_id,
                })
              }
              out.sort((a, b) => (a.due_today === b.due_today ? 0 : a.due_today ? -1 : 1))
              return out
            })()
          : undefined

      groups.push({
        key,
        source,
        displayTitle,
        parent_name: source === 'media' ? arr[0].parent_name || '(No parent)' : undefined,
        company_id: source === 'independent' ? arr[0].company_id : undefined,

        status: statusBest.status,
        status_label: statusBest.status_label,
        due_today,
        next_step_date,
        monthly_visits,
        syndication,

        spoc_name: statusBest.spoc_name || primaryContact?.name || displayTitle,
        contacts_summary: dedupedContacts,
        brands,

        contact_ids,
        advisor_company_ids,
        planned_preview,
      })
    }

    // Sort: due today first, then next step date, then alphabetically.
    groups.sort((a, b) => {
      if (a.due_today !== b.due_today) return a.due_today ? -1 : 1
      const ad = parseDisplayDate(a.next_step_date)
      const bd = parseDisplayDate(b.next_step_date)
      if (ad && bd && ad.getTime() !== bd.getTime()) return ad.getTime() - bd.getTime()
      return a.displayTitle.localeCompare(b.displayTitle)
    })
    return groups
  }, [rows, crmSource, statusOptions])

  const kpiCounts = useMemo(() => {
    const total = companyGroups.length
    const inCampaign = companyGroups.filter((g) => g.status === statusBuckets.inCampaign).length
    const responded = companyGroups.filter((g) => statusBuckets.responded.includes(g.status)).length
    const no_response = companyGroups.filter((g) => g.status === statusBuckets.notContacted).length
    const won = companyGroups.filter((g) => g.status === statusBuckets.won).length
    return { total, inCampaign, responded, no_response, won }
  }, [companyGroups, statusBuckets])

  const dueCount = useMemo(() => {
    const filters = dueDisplayFilters.length ? dueDisplayFilters : [todayDisplay]
    return companyGroups.filter((g) => filters.includes(g.next_step_date)).length
  }, [companyGroups, dueDisplayFilters, todayDisplay])

  const visibleGroups = useMemo(() => {
    const k = activeKpiKey
    let base: CompanyGroup[]
    if (k === 'total') base = companyGroups
    else if (k === 'in_campaign') base = companyGroups.filter((g) => g.status === statusBuckets.inCampaign)
    else if (k === 'responded') base = companyGroups.filter((g) => statusBuckets.responded.includes(g.status))
    else if (k === 'no_response') base = companyGroups.filter((g) => g.status === statusBuckets.notContacted)
    else if (k === 'won') base = companyGroups.filter((g) => g.status === statusBuckets.won)
    else if (k === 'due') base = companyGroups.filter((g) => dueDisplayFilters.includes(g.next_step_date))
    else base = companyGroups

    const q = crmSearchQuery.trim().toLowerCase()
    if (!q) return base

    const matchesIndependent = (g: CompanyGroup) => {
      const title = (g.displayTitle || '').toLowerCase()
      if (title.includes(q)) return true
      const spocs = (g.contacts_summary || []).filter((c) => c.kind === 'spoc')
      return spocs.some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
    }

    const matchesMedia = (g: CompanyGroup) => {
      const title = (g.displayTitle || '').toLowerCase()
      if (title.includes(q)) return true
      if (!g.brands?.length) return false
      return g.brands.some((b) => {
        if ((b.company_domain || '').toLowerCase().includes(q)) return true
        return (b.contacts_summary || [])
          .filter((c) => c.kind === 'spoc')
          .some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
      })
    }

    return base.filter((g) => (crmSource === 'media' ? matchesMedia(g) : matchesIndependent(g)))
  }, [companyGroups, activeKpiKey, statusBuckets, dueDisplayFilters, crmSearchQuery, crmSource])

  useEffect(() => {
    // Close drawer if the selected group is no longer in the current filtered view.
    if (selectedGroup && !visibleGroups.some((g) => g.key === selectedGroup.key)) {
      setSelectedGroup(null)
      setDetailTab('overview')
    }

    if (crmSource === 'media' && selectedMediaBrand) {
      const parentKey = selectedMediaBrand.parent.key
      if (!visibleGroups.some((g) => g.key === parentKey)) {
        setSelectedMediaBrand(null)
        setDetailTab('overview')
      }
    }
  }, [visibleGroups, selectedGroup, crmSource, selectedMediaBrand])

  return (
    <div className="page-wide dashboard-crm">
      <header className="crm-header">
        <div className="crm-header-left">
          <p className="intel-eyebrow">Publisher CRM</p>
          <h1>LH2 Publisher CRM</h1>
        </div>
        <div className="crm-header-right">
          <button type="button" className="secondary sm" disabled={busy} title="UI only in this build">
            + Add Publisher
          </button>
        </div>
      </header>

      {err && <p className="error">{err}</p>}

      <div className="crm-controls">
        <div className="crm-tabs" aria-label="Offer tabs">
          <button
            type="button"
            className={offerTab === 'indication' ? 'crm-tab active' : 'crm-tab'}
            onClick={() => setOfferTab('indication')}
            disabled={busy}
          >
            Syndication
          </button>
          <button
            type="button"
            className={offerTab === 'general_acq' ? 'crm-tab active' : 'crm-tab'}
            onClick={() => setOfferTab('general_acq')}
            disabled={busy}
          >
            General-Acq
          </button>
        </div>

        <div className="crm-source">
          <label className="muted small">Source</label>
          <div className="crm-seg">
            <button
              type="button"
              className={crmSource === 'independent' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => setCrmSource('independent')}
              disabled={busy}
            >
              Independent
            </button>
            <button
              type="button"
              className={crmSource === 'media' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => setCrmSource('media')}
              disabled={busy}
            >
              Media Houses
            </button>
          </div>
        </div>
      </div>

      {busy && companyGroups.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="crm-kpi-row kpi-row">
          {(
            [
              { key: 'total' as const, label: 'Total', value: kpiCounts.total, activeColor: '#374151' },
              { key: 'in_campaign' as const, label: 'In campaign', value: kpiCounts.inCampaign, activeColor: '#2563EB' },
              { key: 'responded' as const, label: 'Responded', value: kpiCounts.responded, activeColor: '#0891B2' },
              { key: 'no_response' as const, label: 'No response', value: kpiCounts.no_response, activeColor: '#D97706' },
              { key: 'won' as const, label: 'Won', value: kpiCounts.won, activeColor: '#16A34A' },
              { key: 'due' as const, label: 'Due', value: dueCount, activeColor: 'var(--accent)' },
            ] as { key: KpiKey; label: string; value: number; activeColor: string }[]
          ).map((c) => {
            const isActive = activeKpiKey === c.key
            return (
              <button
                key={c.key}
                type="button"
                className={[
                  'kpi crm-kpi crm-kpi-btn',
                  isActive ? 'active' : '',
                  `crm-kpi-${c.key}`,
                ].filter(Boolean).join(' ')}
                onClick={() => {
                  if (c.key === 'due') {
                    if (isActive) {
                      setActiveKpiKey('total')
                      setDueFilterOpen(false)
                      setDueDisplayFilters([todayDisplay])
                      setCustomDueDate('')
                      setIncludeCustomDue(false)
                    } else {
                      setActiveKpiKey('due')
                      setDueFilterOpen(true)
                      setDueDisplayFilters((prev) => {
                        const base = uniqueBy(prev.length ? prev : [todayDisplay], (x) => x)
                        if (!base.includes(todayDisplay)) base.push(todayDisplay)
                        return base
                      })
                    }
                  } else {
                    if (activeKpiKey === 'due') setDueFilterOpen(false)
                    setActiveKpiKey(isActive ? 'total' : c.key)
                  }
                }}
                aria-label={`Filter by ${c.label}`}
                disabled={busy}
              >
                <span className="kpi-n">{c.value}</span>
                <span className="kpi-l">{c.label}</span>
              </button>
            )
          })}
        </div>
      )}

      {dueFilterOpen && activeKpiKey === 'due' && (
        <div className="crm-due-filter">
          <div className="crm-due-filter-row">
            <label className="crm-due-chk">
              <input
                type="checkbox"
                checked={dueDisplayFilters.includes(todayDisplay)}
                onChange={(ev) => {
                  const checked = ev.target.checked
                  setDueDisplayFilters((arr) => (checked ? uniqueBy([...arr, todayDisplay], (x) => x) : arr.filter((x) => x !== todayDisplay)))
                }}
              />
              Today
            </label>
            <label className="crm-due-chk">
              <input
                type="checkbox"
                checked={dueDisplayFilters.includes(tomorrowDisplay)}
                onChange={(ev) => {
                  const checked = ev.target.checked
                  setDueDisplayFilters((arr) =>
                    checked ? uniqueBy([...arr, tomorrowDisplay], (x) => x) : arr.filter((x) => x !== tomorrowDisplay),
                  )
                }}
              />
              Tomorrow
            </label>

            <div className="crm-due-custom">
              <input
                type="date"
                className="crm-due-date"
                value={customDueDate}
                title="Pick custom due date"
                aria-label="Pick custom due date"
                onChange={(e) => setCustomDueDate(e.target.value)}
              />
              <label className="crm-due-chk crm-due-include">
                <input
                  type="checkbox"
                  checked={includeCustomDue}
                  disabled={!customDueDisplay}
                  onChange={(ev) => {
                    const checked = ev.target.checked
                    setIncludeCustomDue(checked)
                    if (!checked) {
                      setDueDisplayFilters((arr) => arr.filter((x) => x !== customDueDisplay))
                    }
                  }}
                />
                Include
              </label>
            </div>
          </div>
          <div className="crm-due-filter-hint muted small">Filters by the “first upcoming planned step” date.</div>
        </div>
      )}

      <div ref={crmGridWrapRef} className={selectedGroup || selectedMediaBrand ? 'crm-grid drawer-open' : 'crm-grid'}>
        <section className="panel crm-left">
          <div className="crm-left-head">
            <h2>Publishers</h2>
            <div className="crm-search">
              <input
                className="crm-search-input"
                type="text"
                value={crmSearchQuery}
                placeholder="Search companies / SPOCs"
                onChange={(e) => setCrmSearchQuery(e.target.value)}
                aria-label="Search companies and SPOCs"
              />
            </div>
            <span className="muted small">
              {visibleGroups.length} {crmSource === 'media' ? 'media groups' : 'companies'}
            </span>
          </div>
          <div className="lh2-table-wrap crm-table-wrap">
            <table className="lh2-data-table crm-table">
              <thead>
                {crmSource === 'media' ? (
                  <tr>
                    <th>Media House</th>
                    <th>Brands</th>
                    <th>Contacted</th>
                    <th>CEO / Decision Maker</th>
                    <th>Total Traffic</th>
                    <th>Syndication</th>
                  </tr>
                ) : (
                  <tr>
                    <th>Website</th>
                    <th>SPOC name</th>
                    <th>Status</th>
                    <th>Next step</th>
                    <th>Traffic</th>
                    <th>Syndication</th>
                  </tr>
                )}
              </thead>
              <tbody>
                          {visibleGroups.length === 0 && (
                  <tr>
                    <td className="muted" colSpan={crmSource === 'media' ? 6 : 6}>
                      No companies match this view{crmSearchQuery.trim() ? ' / search' : ''}.
                    </td>
                  </tr>
                )}
                {visibleGroups.map((g) => {
                  // MEDIA: parent row expands into a child table; child selection opens the drawer.
                  if (crmSource === 'media') {
                    const expanded = expandedMediaHouseKey === g.key
                    const q = crmSearchQuery.trim().toLowerCase()
                    const brandsToShow = !q
                      ? g.brands
                      : (g.brands || []).filter((b) => {
                          const bt = (b.company_domain || '').toLowerCase()
                          if (bt.includes(q)) return true
                          return (b.contacts_summary || [])
                            .filter((c) => c.kind === 'spoc')
                            .some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
                        })
                    const contactedCount = brandsToShow ? brandsToShow.filter((b) => b.status !== statusBuckets.notContacted).length : 0
                    const ceoContact = (g.contacts_summary || []).find((c) => c.kind === 'contact')
                    return (
                      <Fragment key={g.key}>
                        <tr
                          className={expanded ? 'crm-row active' : 'crm-row'}
                          tabIndex={0}
                          role="button"
                          onClick={() => {
                            setExpandedMediaHouseKey(expanded ? null : g.key)
                          }}
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
                          <td>{brandsToShow?.length || 0}</td>
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

                        {expanded && brandsToShow && (
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
                                    {brandsToShow.map((b) => {
                                      const spocs = (b.contacts_summary || []).filter((c) => c.kind === 'spoc')
                                      const spocName = formatSpocSummary(spocs)
                                      const activeChild = selectedMediaBrand?.brand.company_domain === b.company_domain
                                      return (
                                        <tr
                                          key={String(b.advisor_company_id || '') + b.company_domain}
                                          className={activeChild ? 'crm-row active' : 'crm-row'}
                                          tabIndex={0}
                                          role="button"
                                          onClick={(e) => {
                                            const gridEl = crmGridWrapRef.current
                                            if (gridEl) {
                                              const rowRect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                                              const gridRect = gridEl.getBoundingClientRect()
                                              const next = rowRect.top - gridRect.top
                                              setDrawerYOffsetPx(Math.max(0, Math.min(next, 5000)))
                                            }
                                            captureScrollTop()
                                            setSelectedMediaBrand({ parent: g, brand: b })
                                            setDetailTab('overview')
                                            setExpandedMediaHouseKey(g.key)
                                          }}
                                          aria-label={`Select child ${b.company_domain}`}
                                        >
                                          <td>
                                            <strong>{b.company_domain}</strong>
                                          </td>
                                          <td>
                                            <div>{spocName}</div>
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
                  }

                  // INDEPENDENT: one row per company group, drawer opens immediately.
                  const active = selectedGroup?.key === g.key
                  const spocs = (g.contacts_summary || []).filter((c) => c.kind === 'spoc')
                  const spocSummary = formatSpocSummary(spocs)
                  return (
                    <tr
                      key={g.key}
                      className={active ? 'crm-row active' : 'crm-row'}
                      tabIndex={0}
                      role="button"
                      onClick={(e) => {
                        const gridEl = crmGridWrapRef.current
                        if (gridEl) {
                          const rowRect = (e.currentTarget as HTMLElement | null)?.getBoundingClientRect?.()
                          if (rowRect) {
                            const gridRect = gridEl.getBoundingClientRect()
                            const next = rowRect.top - gridRect.top
                            setDrawerYOffsetPx(Math.max(0, Math.min(next, 5000)))
                          } else {
                            setDrawerYOffsetPx(0)
                          }
                        }
                        captureScrollTop()
                        setSelectedGroup(g)
                        setDetailTab('overview')
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          const gridEl = crmGridWrapRef.current
                          if (gridEl) {
                            const rowRect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                            const gridRect = gridEl.getBoundingClientRect()
                            const next = rowRect.top - gridRect.top
                            setDrawerYOffsetPx(Math.max(0, Math.min(next, 5000)))
                          } else {
                            setDrawerYOffsetPx(0)
                          }
                          captureScrollTop()
                          setSelectedGroup(g)
                          setDetailTab('overview')
                        }
                      }}
                      aria-label={`Select ${g.displayTitle}`}
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
        </section>

        {crmSource !== 'media' && selectedGroup && (
              <aside className="panel crm-right drawer-panel" style={drawerVars}>
            <div className="crm-right-head">
              <h2 className="crm-right-title">{selectedGroup.displayTitle}</h2>
              <div className="crm-right-tabs">
                <button
                  type="button"
                  className={detailTab === 'overview' ? 'crm-tab active' : 'crm-tab'}
                  onClick={() => setDetailTab('overview')}
                >
                  Overview
                </button>
                <button
                  type="button"
                  className={detailTab === 'campaign' ? 'crm-tab active' : 'crm-tab'}
                  onClick={() => setDetailTab('campaign')}
                >
                  Campaign &amp; Outreach
                </button>
                <button
                  type="button"
                  className="crm-close-btn"
                  onClick={() => {
                    captureScrollTop()
                    setDrawerYOffsetPx(0)
                    setSelectedGroup(null)
                    setDrawerHasResearch(null)
                    setDrawerResearchBusy(false)
                  }}
                  aria-label="Close drawer"
                >
                  ×
                </button>
              </div>
            </div>

            {detailTab === 'overview' ? (
              <div className="crm-detail">
                <div className="crm-detail-row">
                  <div className="muted small">Research</div>
                  {drawerResearchBusy ? (
                    <div className="muted">Checking…</div>
                  ) : drawerHasResearch ? (
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() => {
                        clearWorkspace()
                        setSelection({
                          source: 'independent',
                          companyId: selectedGroup.company_id,
                          contactId: null,
                          advisorCompanyId: null,
                          sessionId: null,
                        })
                        navigate('/intelligence')
                      }}
                    >
                      Go to research
                    </button>
                  ) : (
                    <div>
                      <div className="muted small">Research pending</div>
                      <button
                        type="button"
                        className="secondary sm"
                        onClick={() => {
                          clearWorkspace()
                          setSelection({
                            source: 'independent',
                            companyId: selectedGroup.company_id,
                            contactId: null,
                            advisorCompanyId: null,
                            sessionId: null,
                          })
                          navigate('/intelligence')
                        }}
                      >
                        Head to intelligence
                      </button>
                    </div>
                  )}
                </div>

                <div className="crm-detail-row">
                  <div className="muted small">Primary SPOC</div>
                  <div>
                    <strong>{selectedGroup.spoc_name}</strong>
                  </div>
                </div>

                <div className="crm-detail-row">
                  <div className="muted small">Current status</div>
                  <div>{selectedGroup.status_label}</div>
                </div>

                <div className="crm-detail-row">
                  <div className="muted small">Traffic</div>
                  <div>{fmtVisits(selectedGroup.monthly_visits)}</div>
                </div>

                <div className="crm-detail-row">
                  <div className="muted small">Syndication</div>
                  <div>
                    <SyndicationBadges syndication={selectedGroup.syndication} />
                  </div>
                </div>

                <div className="crm-detail-row">
                  <div className="muted small">Next step</div>
                  <div>
                    {selectedGroup.next_step_date}
                    {selectedGroup.due_today ? <div className="crm-due-today">Due</div> : null}
                  </div>
                </div>

                {crmSource === 'independent' ? (
                  <div className="crm-detail-row">
                    <div className="muted small">Contacts & SPOCs</div>
                    <div className="crm-contact-list">
                      {(selectedGroup.contacts_summary || []).length === 0 ? (
                        <div className="muted">No contacts yet.</div>
                      ) : (
                        selectedGroup.contacts_summary.map((c, idx) => (
                          <div key={`${c.kind}-${c.email}-${c.linkedin_url}-${idx}`} className="crm-contact-item">
                            <div>
                              <strong>{c.name}</strong> <span className="muted small">({c.kind})</span>
                            </div>
                            <div className="muted small">{c.role || '—'}</div>
                            {c.email ? <div className="muted small">{c.email}</div> : null}
                            {c.linkedin_url ? (
                              <a href={c.linkedin_url} target="_blank" rel="noopener noreferrer">
                                LinkedIn
                              </a>
                            ) : null}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="crm-detail-row">
                    <div className="muted small">Brands & SPOCs</div>
                    <div className="crm-brand-list">
                      {(selectedGroup.brands || []).map((b) => (
                        <details key={b.company_domain} className="crm-brand-details">
                          <summary className="crm-brand-summary">
                            <span className="crm-brand-title">{b.company_domain}</span>
                            <span className="crm-brand-pill">{b.status_label}</span>
                          </summary>
                          <div className="crm-brand-contacts">
                            {(b.contacts_summary || []).filter((c) => c.kind === 'spoc').length === 0 ? (
                              <div className="muted">No SPOCs for this brand yet.</div>
                            ) : (
                              (b.contacts_summary || [])
                                .filter((c) => c.kind === 'spoc')
                                .map((c, idx) => (
                                  <div key={`${c.email}-${c.linkedin_url}-${idx}`} className="crm-contact-item">
                                    <div>
                                      <strong>{c.name}</strong> <span className="muted small">(SPOC)</span>
                                    </div>
                                    <div className="muted small">{c.role || '—'}</div>
                                    {c.email ? <div className="muted small">{c.email}</div> : null}
                                    {c.linkedin_url ? (
                                      <a href={c.linkedin_url} target="_blank" rel="noopener noreferrer">
                                        LinkedIn
                                      </a>
                                    ) : null}
                                  </div>
                                ))
                            )}
                          </div>
                        </details>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="crm-detail">
                <div className="crm-subhead">Update pipeline status</div>
                <div className="crm-status-grid">
                  {statusOptions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={selectedStatus === s ? 'crm-status-btn active' : 'crm-status-btn'}
                      onClick={() => void patchGroupStatus(s)}
                      disabled={busy}
                    >
                      {statusLabels[s] || s}
                    </button>
                  ))}
                </div>

                <div className="crm-subhead">Planned outreach (preview)</div>
                <ul className="crm-plan-list">
                  {(selectedGroup.planned_preview || []).length === 0 && <li className="muted">No planned outreach yet.</li>}
                  {(selectedGroup.planned_preview || []).map((p) => (
                    <li key={String(p.id)} className="crm-plan-item">
                      <div className="crm-plan-title">
                        {p.channel} · {p.sequence_step ? `Step ${p.sequence_step}` : ''}
                      </div>
                      <div className="muted small">{p.scheduled_at ? String(p.scheduled_at).slice(0, 10) : '—'}</div>
                      <div className="crm-plan-desc">{p.description}</div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </aside>
        )}

        {crmSource === 'media' && selectedMediaBrand && (
          <aside className="panel crm-right drawer-panel" style={drawerVars}>
            <div className="crm-right-head">
              <h2 className="crm-right-title">{selectedMediaBrand.brand.company_domain}</h2>
              <div className="crm-right-tabs">
                <button
                  type="button"
                  className={detailTab === 'overview' ? 'crm-tab active' : 'crm-tab'}
                  onClick={() => setDetailTab('overview')}
                >
                  Overview
                </button>
                <button
                  type="button"
                  className={detailTab === 'campaign' ? 'crm-tab active' : 'crm-tab'}
                  onClick={() => setDetailTab('campaign')}
                >
                  Campaign &amp; Outreach
                </button>
                <button
                  type="button"
                  className="crm-close-btn"
                  onClick={() => {
                    captureScrollTop()
                    setDrawerYOffsetPx(0)
                    setSelectedMediaBrand(null)
                    setDrawerHasResearch(null)
                    setDrawerResearchBusy(false)
                  }}
                  aria-label="Close drawer"
                >
                  ×
                </button>
              </div>
            </div>

            {detailTab === 'overview' ? (
              <div className="crm-detail">
                <div className="crm-detail-row">
                  <div className="muted small">Research</div>
                  {drawerResearchBusy ? (
                    <div className="muted">Checking…</div>
                  ) : drawerHasResearch ? (
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() => {
                        clearWorkspace()
                        setSelection({
                          source: 'media',
                          companyId: null,
                          contactId: null,
                          advisorCompanyId: selectedMediaBrand.brand.advisor_company_id,
                          sessionId: null,
                        })
                        navigate('/intelligence')
                      }}
                    >
                      Go to research
                    </button>
                  ) : (
                    <div>
                      <div className="muted small">Research pending</div>
                      <button
                        type="button"
                        className="secondary sm"
                        onClick={() => {
                          clearWorkspace()
                          setSelection({
                            source: 'media',
                            companyId: null,
                            contactId: null,
                            advisorCompanyId: selectedMediaBrand.brand.advisor_company_id,
                            sessionId: null,
                          })
                          navigate('/intelligence')
                        }}
                      >
                        Head to intelligence
                      </button>
                    </div>
                  )}
                </div>

                {(() => {
                  const brand = selectedMediaBrand.brand
                  const spocs = (brand.contacts_summary || []).filter((c) => c.kind === 'spoc')
                  const primarySpoc = spocs.find((s) => (s.email || '').trim() || (s.linkedin_url || '').trim()) || spocs[0]
                  const ceo = (brand.contacts_summary || []).find((c) => c.kind === 'contact')
                  return (
                    <>
                      <div className="crm-detail-row">
                        <div className="muted small">Primary SPOC</div>
                        <div>
                          <strong>{primarySpoc?.name || '—'}</strong>
                          {primarySpoc?.role && primarySpoc.role !== '—' ? <div className="muted small">{primarySpoc.role}</div> : null}
                        </div>
                      </div>

                      {ceo ? (
                        <div className="crm-detail-row">
                          <div className="muted small">CEO / Decision Maker</div>
                          <div>
                            <strong>{ceo.name}</strong>
                            {ceo.role && ceo.role !== '—' ? <div className="muted small">{ceo.role}</div> : null}
                          </div>
                        </div>
                      ) : null}

                      <div className="crm-detail-row">
                        <div className="muted small">Current status</div>
                        <div>{brand.status_label}</div>
                      </div>

                      <div className="crm-detail-row">
                        <div className="muted small">Contacted</div>
                        <div>{brand.status !== statusBuckets.notContacted ? 'Yes' : 'No'}</div>
                      </div>

                      <div className="crm-detail-row">
                        <div className="muted small">Traffic</div>
                        <div>{fmtVisits(brand.monthly_visits)}</div>
                      </div>

                      <div className="crm-detail-row">
                        <div className="muted small">Syndication</div>
                        <div>
                          <SyndicationBadges syndication={brand.syndication} />
                        </div>
                      </div>

                      <div className="crm-detail-row">
                        <div className="muted small">Next step</div>
                        <div>
                          {brand.next_step_date}
                          {brand.due_today ? <div className="crm-due-today">Due</div> : null}
                        </div>
                      </div>

                      <div className="crm-detail-row">
                        <div className="muted small">Contacts (SPOCs)</div>
                        <div className="crm-contact-list">
                          {spocs.length === 0 ? (
                            <div className="muted">No SPOC contacts yet.</div>
                          ) : (
                            spocs.map((c, idx) => (
                              <div key={`${c.kind}-${c.email}-${c.linkedin_url}-${idx}`} className="crm-contact-item">
                                <div>
                                  <strong>{c.name}</strong>
                                </div>
                                {c.role && c.role !== '—' ? <div className="muted small">{c.role}</div> : null}
                                {c.email ? <div className="muted small">{c.email}</div> : null}
                                {c.linkedin_url ? (
                                  <a href={c.linkedin_url} target="_blank" rel="noopener noreferrer">
                                    LinkedIn
                                  </a>
                                ) : null}
                              </div>
                            ))
                          )}
                        </div>
                      </div>
                    </>
                  )
                })()}
              </div>
            ) : (
              <div className="crm-detail">
                <div className="crm-subhead">Update pipeline status</div>
                <div className="crm-status-grid">
                  {statusOptions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={selectedStatus === s ? 'crm-status-btn active' : 'crm-status-btn'}
                      onClick={() => void patchMediaBrandStatus(s)}
                      disabled={busy}
                    >
                      {statusLabels[s] || s}
                    </button>
                  ))}
                </div>

                <div className="crm-subhead">Planned outreach (preview)</div>
                <p className="muted">Planned outreach preview is available at the media-house level for now.</p>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
