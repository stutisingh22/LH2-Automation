import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '../api/client'
import {
  mapApiPublisherGroup as mapApiGroup,
  type ApiPublisherGroup,
  type UiPublisherBrand as BrandSummary,
  type UiPublisherGroup as CompanyGroup,
} from '../api/publisherCrm'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useNavigate, useSearchParams } from 'react-router-dom'

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

const DEFAULT_PAGE_SIZE = 50
const MIN_PAGE_SIZE = 10
const MAX_PAGE_SIZE = 200

const ALL_KPI_KEYS: readonly KpiKey[] = [
  'total',
  'in_campaign',
  'responded',
  'no_response',
  'won',
  'due',
]

function clampPageSize(n: number): number {
  if (!Number.isFinite(n)) return DEFAULT_PAGE_SIZE
  return Math.min(MAX_PAGE_SIZE, Math.max(MIN_PAGE_SIZE, Math.round(n)))
}

function parseKpiParam(v: string | null): KpiKey {
  if (v && (ALL_KPI_KEYS as readonly string[]).includes(v)) return v as KpiKey
  return 'total'
}

function dueNeedsUrlParam(activeKpiKey: KpiKey, dueDisplayFilters: string[], todayDisplay: string): boolean {
  if (activeKpiKey === 'due') return true
  if (dueDisplayFilters.length !== 1) return true
  return dueDisplayFilters[0] !== todayDisplay
}

/** Compare query strings regardless of param ordering (avoids setSearchParams loops). */
function dashboardUrlParamsEqual(a: URLSearchParams, b: URLSearchParams): boolean {
  const keys = new Set<string>([...a.keys(), ...b.keys()])
  for (const k of keys) {
    if (a.get(k) !== b.get(k)) return false
  }
  return true
}

/** UTC "Mar 25, 2026" matching dashboard due-date strings (must match in-component formatter). */
const _UTC_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const
function _utcPad2(n: number) {
  return String(n).padStart(2, '0')
}
function formatUtcDisplayStatic(d: Date) {
  const m = _UTC_MONTHS[d.getUTCMonth()]
  const day = _utcPad2(d.getUTCDate())
  const y = d.getUTCFullYear()
  return `${m} ${day}, ${y}`
}
function getTodayUtcDisplayStatic() {
  const now = new Date()
  const todayUtc = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  return formatUtcDisplayStatic(todayUtc)
}

/** Parse /dashboard query string once on mount so list filters + page match the URL before first fetch. */
function parseDashboardUrlSearch(search: string): {
  crmSource: 'independent' | 'media'
  listPage: number
  pageSize: number
  offerTab: 'indication' | 'general_acq'
  activeKpiKey: KpiKey
  dueDisplayFilters: string[]
  detailTab: 'overview' | 'campaign'
  crmSearchInput: string
  debouncedSearch: string
} {
  const raw = search.startsWith('?') ? search.slice(1) : search
  const sp = new URLSearchParams(raw)
  const todayDisplay = getTodayUtcDisplayStatic()
  const crmSource: 'independent' | 'media' = sp.get('source') === 'media' ? 'media' : 'independent'
  const listPage = Math.max(1, parseInt(sp.get('page') || '1', 10) || 1)
  const pageSize = clampPageSize(parseInt(sp.get('limit') || String(DEFAULT_PAGE_SIZE), 10) || DEFAULT_PAGE_SIZE)
  const offerTab: 'indication' | 'general_acq' = sp.get('offer') === 'general_acq' ? 'general_acq' : 'indication'
  const activeKpiKey = parseKpiParam(sp.get('kpi'))
  const dueRaw = sp.get('due')
  const dueParsed = dueRaw && dueRaw.length ? dueRaw.split('|').map((s) => s.trim()).filter(Boolean) : null
  const dueDisplayFilters = dueParsed && dueParsed.length ? dueParsed : [todayDisplay]
  const detailTab: 'overview' | 'campaign' = sp.get('detail') === 'campaign' ? 'campaign' : 'overview'
  const qParam = sp.get('q') ?? ''
  return {
    crmSource,
    listPage,
    pageSize,
    offerTab,
    activeKpiKey,
    dueDisplayFilters,
    detailTab,
    crmSearchInput: qParam,
    debouncedSearch: qParam.trim(),
  }
}

function minimalIndependentGroup(companyId: number): CompanyGroup {
  return {
    key: `company:${companyId}`,
    source: 'independent',
    displayTitle: '…',
    company_id: companyId,
    status: '',
    status_label: '—',
    due_today: false,
    next_step_date: '—',
    monthly_visits: null,
    syndication: [],
    spoc_name: '',
    contacts_summary: [],
    contact_ids: [],
    advisor_company_ids: [],
    planned_preview: [],
  }
}

function minimalMediaBrandSelection(expandKey: string | null, advisorId: number): { parent: CompanyGroup; brand: BrandSummary } {
  const pk = expandKey?.trim() || 'parent:(unknown)'
  const parentName = pk.startsWith('parent:') ? pk.slice(7) : '(unknown)'
  const parent: CompanyGroup = {
    key: pk,
    source: 'media',
    displayTitle: parentName,
    parent_name: parentName,
    status: '',
    status_label: '—',
    due_today: false,
    next_step_date: '—',
    monthly_visits: null,
    syndication: [],
    spoc_name: '',
    contacts_summary: [],
    brands: [],
    contact_ids: [],
    advisor_company_ids: [],
    planned_preview: [],
  }
  const brand: BrandSummary = {
    company_domain: '…',
    advisor_company_id: advisorId,
    status: '',
    status_label: '—',
    due_today: false,
    next_step_date: '—',
    monthly_visits: null,
    syndication: [],
    spoc_name: '',
    contacts_summary: [],
  }
  return { parent, brand }
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
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedCompanyIdRef = useRef<number | undefined>(undefined)
  const selectedAdvisorBrandIdRef = useRef<number | null>(null)

  const [initialDash] = useState(() =>
    parseDashboardUrlSearch(typeof window !== 'undefined' ? window.location.search : ''),
  )

  const [crmSource, setCrmSource] = useState<'independent' | 'media'>(initialDash.crmSource)
  const [offerTab, setOfferTab] = useState<'indication' | 'general_acq'>(initialDash.offerTab)

  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [companyGroups, setCompanyGroups] = useState<CompanyGroup[]>([])
  const [listTotal, setListTotal] = useState(0)
  const [listPage, setListPage] = useState(initialDash.listPage)
  const [pageSize, setPageSize] = useState(initialDash.pageSize)
  const [apiKpis, setApiKpis] = useState({
    total: 0,
    in_campaign: 0,
    responded: 0,
    no_response: 0,
    won: 0,
    due_today: 0,
  })

  const [activeKpiKey, setActiveKpiKey] = useState<KpiKey>(initialDash.activeKpiKey)
  const [selectedGroup, setSelectedGroup] = useState<CompanyGroup | null>(null)
  const [expandedMediaHouseKey, setExpandedMediaHouseKey] = useState<string | null>(null)
  const [mediaBrandsLoadingKey, setMediaBrandsLoadingKey] = useState<string | null>(null)
  const [listDataEpoch, setListDataEpoch] = useState(0)
  const [selectedMediaBrand, setSelectedMediaBrand] = useState<null | { parent: CompanyGroup; brand: BrandSummary }>(null)
  const [detailTab, setDetailTab] = useState<'overview' | 'campaign'>(initialDash.detailTab)
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
  const [dueDisplayFilters, setDueDisplayFilters] = useState<string[]>(initialDash.dueDisplayFilters)
  const [customDueDate, setCustomDueDate] = useState<string>('') // yyyy-mm-dd
  const [includeCustomDue, setIncludeCustomDue] = useState(false)

  const [drawerHasResearch, setDrawerHasResearch] = useState<boolean | null>(null)
  const [drawerResearchBusy, setDrawerResearchBusy] = useState(false)

  const [crmSearchInput, setCrmSearchInput] = useState(initialDash.crmSearchInput)
  const [debouncedSearch, setDebouncedSearch] = useState(initialDash.debouncedSearch)
  const lastDetailFetchKey = useRef<string | null>(null)
  const companyGroupsRef = useRef(companyGroups)
  companyGroupsRef.current = companyGroups
  const listFilterKey = useMemo(
    () =>
      [crmSource, offerTab, debouncedSearch, activeKpiKey, dueDisplayFilters.join('\0'), pageSize].join('|'),
    [crmSource, offerTab, debouncedSearch, activeKpiKey, dueDisplayFilters, pageSize],
  )
  const prevListFilterKey = useRef<string | null>(null)
  const lastListFetchSig = useRef<string>('')

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

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(crmSearchInput.trim()), 320)
    return () => window.clearTimeout(t)
  }, [crmSearchInput])

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

  const fetchCrm = async (page: number, opts?: { clearSelection?: boolean }) => {
    setBusy(true)
    setErr('')
    try {
      const q = new URLSearchParams()
      q.set('source', crmSource)
      q.set('offer_tab', offerTab)
      q.set('page', String(page))
      q.set('page_size', String(pageSize))
      if (debouncedSearch) q.set('search', debouncedSearch)
      q.set('kpi', activeKpiKey === 'due' ? 'due' : activeKpiKey)
      const duePipe = (dueDisplayFilters.length ? dueDisplayFilters : [todayDisplay]).join('|')
      q.set('due_dates', duePipe)
      q.set('compact', 'true')

      const out = await apiFetch<{
        kpis: Record<string, number>
        groups: ApiPublisherGroup[]
        total: number
        page: number
        page_size: number
      }>(`/api/dashboard/publisher-crm?${q.toString()}`)

      setApiKpis({
        total: Number(out.kpis?.total ?? 0),
        in_campaign: Number(out.kpis?.in_campaign ?? 0),
        responded: Number(out.kpis?.responded ?? 0),
        no_response: Number(out.kpis?.no_response ?? 0),
        won: Number(out.kpis?.won ?? 0),
        due_today: Number(out.kpis?.due_today ?? 0),
      })
      setListTotal(Number(out.total ?? 0))
      setListPage(Number(out.page ?? page))
      setCompanyGroups((out.groups || []).map(mapApiGroup))
      if (opts?.clearSelection) {
        setSelectedGroup(null)
        setExpandedMediaHouseKey(null)
        setSelectedMediaBrand(null)
        lastDetailFetchKey.current = null
      }
      setDrawerHasResearch(null)
      setDrawerResearchBusy(false)
      setListDataEpoch((n) => n + 1)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to load dashboard')
    } finally {
      setBusy(false)
    }
  }

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
        const q = new URLSearchParams()
        q.set('source', 'media')
        q.set('offer_tab', offerTab)
        q.set('parent_key', expandedMediaHouseKey)
        const out = await apiFetch<{ group: ApiPublisherGroup | null }>(
          `/api/dashboard/publisher-crm/detail?${q.toString()}`,
        )
        if (cancelled || !out.group) return
        const ui = mapApiGroup(out.group)
        setCompanyGroups((prev) =>
          prev.map((x) => (x.key === expandedMediaHouseKey ? { ...x, brands: ui.brands ?? [] } : x)),
        )
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load media brands')
      } finally {
        if (!cancelled) setMediaBrandsLoadingKey(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [crmSource, expandedMediaHouseKey, offerTab, listDataEpoch])

  useEffect(() => {
    selectedCompanyIdRef.current = selectedGroup?.company_id
  }, [selectedGroup?.company_id])

  useEffect(() => {
    selectedAdvisorBrandIdRef.current = selectedMediaBrand?.brand.advisor_company_id ?? null
  }, [selectedMediaBrand?.brand.advisor_company_id])

  useLayoutEffect(() => {
    const qParam = searchParams.get('q') ?? ''
    const page = Math.max(1, parseInt(searchParams.get('page') || '1', 10) || 1)
    const limit = clampPageSize(parseInt(searchParams.get('limit') || String(DEFAULT_PAGE_SIZE), 10) || DEFAULT_PAGE_SIZE)
    const source: 'independent' | 'media' = searchParams.get('source') === 'media' ? 'media' : 'independent'
    const offer: 'indication' | 'general_acq' =
      searchParams.get('offer') === 'general_acq' ? 'general_acq' : 'indication'
    const kpi = parseKpiParam(searchParams.get('kpi'))
    const dueRaw = searchParams.get('due')
    const dueParsed =
      dueRaw && dueRaw.length ? dueRaw.split('|').map((s) => s.trim()).filter(Boolean) : null

    setCrmSearchInput(qParam)
    setDebouncedSearch(qParam.trim())
    setListPage(page)
    setPageSize(limit)
    setCrmSource(source)
    setOfferTab(offer)
    setActiveKpiKey(kpi)
    setDueDisplayFilters(dueParsed && dueParsed.length ? dueParsed : [todayDisplay])

    const detailParam = searchParams.get('detail') === 'campaign' ? 'campaign' : 'overview'
    setDetailTab(detailParam)

    if (source === 'independent') {
      setSelectedMediaBrand(null)
      setExpandedMediaHouseKey(null)
      const cidRaw = searchParams.get('company')
      if (cidRaw) {
        const cid = parseInt(cidRaw, 10)
        if (cid > 0 && selectedCompanyIdRef.current !== cid) {
          lastDetailFetchKey.current = null
          setSelectedGroup(minimalIndependentGroup(cid))
        }
      } else {
        setSelectedGroup(null)
      }
    } else {
      setSelectedGroup(null)
      const expandKey = searchParams.get('expand')
      if (expandKey) setExpandedMediaHouseKey(expandKey)
      else setExpandedMediaHouseKey(null)
      const brandRaw = searchParams.get('brand')
      if (brandRaw) {
        const aid = parseInt(brandRaw, 10)
        if (aid > 0 && selectedAdvisorBrandIdRef.current !== aid) {
          lastDetailFetchKey.current = null
          setSelectedMediaBrand(minimalMediaBrandSelection(expandKey, aid))
        }
      } else {
        setSelectedMediaBrand(null)
      }
    }
  }, [searchParams, todayDisplay])

  useEffect(() => {
    const p = new URLSearchParams()
    p.set('source', crmSource)
    p.set('page', String(listPage))
    if (debouncedSearch) p.set('q', debouncedSearch)
    if (pageSize !== DEFAULT_PAGE_SIZE) p.set('limit', String(pageSize))
    if (offerTab === 'general_acq') p.set('offer', 'general_acq')
    if (activeKpiKey !== 'total') p.set('kpi', activeKpiKey)
    if (dueNeedsUrlParam(activeKpiKey, dueDisplayFilters, todayDisplay)) {
      p.set('due', dueDisplayFilters.join('|'))
    }
    if (crmSource === 'independent' && selectedGroup?.company_id) {
      p.set('company', String(selectedGroup.company_id))
    }
    if (crmSource === 'media') {
      if (expandedMediaHouseKey) p.set('expand', expandedMediaHouseKey)
      if (selectedMediaBrand?.brand.advisor_company_id != null) {
        p.set('brand', String(selectedMediaBrand.brand.advisor_company_id))
      }
    }
    if (detailTab === 'campaign') p.set('detail', 'campaign')

    if (dashboardUrlParamsEqual(p, new URLSearchParams(searchParams))) return
    setSearchParams(p, { replace: true })
  }, [
    debouncedSearch,
    listPage,
    pageSize,
    crmSource,
    offerTab,
    activeKpiKey,
    dueDisplayFilters,
    selectedGroup?.company_id,
    selectedMediaBrand?.brand.advisor_company_id,
    expandedMediaHouseKey,
    detailTab,
    todayDisplay,
    searchParams,
    setSearchParams,
  ])

  useEffect(() => {
    if (searchParams.has('source')) return
    setCrmSource(selection.source === 'media' ? 'media' : 'independent')
  }, [selection.source, searchParams])

  useEffect(() => {
    const fkChanged =
      prevListFilterKey.current !== null && prevListFilterKey.current !== listFilterKey
    prevListFilterKey.current = listFilterKey
    if (fkChanged && listPage !== 1) setListPage(1)
    const pageToFetch = fkChanged ? 1 : listPage
    const sig = `${listFilterKey}@${pageToFetch}`
    if (lastListFetchSig.current === sig) return
    lastListFetchSig.current = sig
    void fetchCrm(pageToFetch)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listFilterKey, listPage])

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
        const out = await apiFetch<{ has_research: boolean }>(
          `/api/intelligence/has-research?${q.toString()}`,
          { silent: true },
        )
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

  useEffect(() => {
    const cid = selectedGroup?.company_id
    if (crmSource !== 'independent' || !cid) return
    const sig = `ind:${cid}`
    if (lastDetailFetchKey.current === sig) return
    let cancelled = false
    ;(async () => {
      try {
        const q = new URLSearchParams()
        q.set('source', 'independent')
        q.set('offer_tab', offerTab)
        q.set('company_id', String(cid))
        const out = await apiFetch<{ group: ApiPublisherGroup | null }>(`/api/dashboard/publisher-crm/detail?${q}`)
        if (cancelled || !out.group) return
        lastDetailFetchKey.current = sig
        setSelectedGroup(mapApiGroup(out.group))
      } catch {
        /* keep list row */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [crmSource, offerTab, selectedGroup?.company_id])

  useEffect(() => {
    const aid = selectedMediaBrand?.brand.advisor_company_id
    if (crmSource !== 'media' || aid == null) return
    const sig = `media:${aid}`
    if (lastDetailFetchKey.current === sig) return
    let cancelled = false
    ;(async () => {
      try {
        const q = new URLSearchParams()
        q.set('source', 'media')
        q.set('offer_tab', offerTab)
        q.set('advisor_company_id', String(aid))
        const out = await apiFetch<{ group: ApiPublisherGroup | null }>(`/api/dashboard/publisher-crm/detail?${q}`)
        if (cancelled || !out.group) return
        lastDetailFetchKey.current = sig
        const mapped = mapApiGroup(out.group)
        const brand = mapped.brands?.find((b) => b.advisor_company_id === aid) ?? mapped.brands?.[0]
        if (brand) setSelectedMediaBrand({ parent: mapped, brand })
      } catch {
        /* keep list row */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [crmSource, offerTab, selectedMediaBrand?.brand.advisor_company_id])

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
            apiFetch(`/api/pipeline/media-houses/companies/${aid}`, {
              method: 'PATCH',
              json: { status },
              silent: true,
            }),
          ),
        )
      } else {
        if (!idsContact.length) throw new Error('No contacts available to update for this company yet.')
        await Promise.all(
          idsContact.map((cid) =>
            apiFetch(`/api/pipeline/contacts/${cid}/status`, {
              method: 'PATCH',
              json: { status },
              silent: true,
            }),
          ),
        )
      }
      lastListFetchSig.current = ''
      await fetchCrm(listPage, { clearSelection: true })
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
      await apiFetch(`/api/pipeline/media-houses/companies/${aid}`, {
        method: 'PATCH',
        json: { status },
        silent: true,
      })
      lastListFetchSig.current = ''
      await fetchCrm(listPage, { clearSelection: true })
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

  const kpiCounts = useMemo(
    () => ({
      total: apiKpis.total,
      inCampaign: apiKpis.in_campaign,
      responded: apiKpis.responded,
      no_response: apiKpis.no_response,
      won: apiKpis.won,
    }),
    [apiKpis],
  )

  const dueCount = apiKpis.due_today

  const visibleGroups = useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase()
    if (!q || crmSource !== 'media') return companyGroups
    return companyGroups.map((g) => {
      if (!g.brands?.length) return g
      const brandsToShow = g.brands.filter((b) => {
        if ((b.company_domain || '').toLowerCase().includes(q)) return true
        return (b.spoc_names || []).some((n) => (n || '').toLowerCase().includes(q))
            || (b.contacts_summary || [])
              .filter((c) => c.kind === 'spoc')
              .some(
                (s) =>
                  (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q),
              )
      })
      if (brandsToShow.length === g.brands.length) return g
      return { ...g, brands: brandsToShow }
    })
  }, [companyGroups, debouncedSearch, crmSource])

  useEffect(() => {
    // Close drawer if the selected group is no longer in the current filtered view.
    if (selectedGroup && !visibleGroups.some((g) => g.key === selectedGroup.key)) {
      lastDetailFetchKey.current = null
      setSelectedGroup(null)
      setDetailTab('overview')
    }

    if (crmSource === 'media' && selectedMediaBrand) {
      const parentKey = selectedMediaBrand.parent.key
      if (!visibleGroups.some((g) => g.key === parentKey)) {
        lastDetailFetchKey.current = null
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
              onClick={() => {
                setCrmSource('independent')
                setSelection({ source: 'independent' })
              }}
              disabled={busy}
            >
              Independent
            </button>
            <button
              type="button"
              className={crmSource === 'media' ? 'crm-seg-btn active' : 'crm-seg-btn'}
              onClick={() => {
                setCrmSource('media')
                setSelection({ source: 'media' })
              }}
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
                value={crmSearchInput}
                placeholder="Search companies / SPOCs"
                onChange={(e) => setCrmSearchInput(e.target.value)}
                aria-label="Search companies and SPOCs"
              />
            </div>
            <span className="muted small">
              {listTotal.toLocaleString()} {crmSource === 'media' ? 'media groups' : 'companies'}
              {listTotal > pageSize
                ? ` · page ${listPage} (${visibleGroups.length} shown)`
                : visibleGroups.length !== listTotal
                  ? ` · ${visibleGroups.length} shown`
                  : ''}
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
                      No companies match this view{debouncedSearch.trim() ? ' / search' : ''}.
                    </td>
                  </tr>
                )}
                {visibleGroups.map((g) => {
                  // MEDIA: parent row expands into a child table; child selection opens the drawer.
                  if (crmSource === 'media') {
                    const expanded = expandedMediaHouseKey === g.key
                    const q = debouncedSearch.trim().toLowerCase()
                    const brandList = g.brands || []
                    const brandsToShow = !q
                      ? brandList
                      : brandList.filter((b) => {
                          const bt = (b.company_domain || '').toLowerCase()
                          if (bt.includes(q)) return true
                          return (b.contacts_summary || [])
                            .filter((c) => c.kind === 'spoc')
                            .some((s) => (s.name || '').toLowerCase().includes(q) || (s.email || '').toLowerCase().includes(q))
                        })
                    const brandCountDisplay = g.brandCount ?? brandList.length
                    const contactedCount =
                      brandList.length > 0
                        ? brandsToShow.filter((b) => b.status !== statusBuckets.notContacted).length
                        : (g.contactedBrandCount ?? 0)
                    const brandsLoading =
                      expanded &&
                      mediaBrandsLoadingKey === g.key &&
                      brandList.length === 0 &&
                      (g.brandCount ?? 0) > 0
                    const ceoContact = (g.contacts_summary || []).find((c) => c.kind === 'contact')
                    const ceoLabel = ceoContact?.name || g.ceoDisplayName || g.spoc_name
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
                          <td>{brandCountDisplay}</td>
                          <td>
                            <span className="muted small">{contactedCount}</span>
                          </td>
                          <td>
                            <div>{ceoLabel}</div>
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
                                {brandsLoading ? (
                                  <p className="muted small" style={{ padding: '0.75rem 1rem', margin: 0 }}>
                                    Loading brands…
                                  </p>
                                ) : brandList.length > 0 && (brandsToShow?.length ?? 0) === 0 ? (
                                  <p className="muted small" style={{ padding: '0.75rem 1rem', margin: 0 }}>
                                    No brands match this search.
                                  </p>
                                ) : (brandsToShow?.length ?? 0) === 0 ? (
                                  <p className="muted small" style={{ padding: '0.75rem 1rem', margin: 0 }}>
                                    No brands in this media house.
                                  </p>
                                ) : (
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
                                    {brandsToShow!.map((b) => {
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
                                )}
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
            <div className="crm-pagination crm-pagination-bar">
              {listTotal > pageSize ? (
                <>
                  <button
                    type="button"
                    className="secondary sm"
                    disabled={busy || listPage <= 1}
                    onClick={() => setListPage((p) => Math.max(1, p - 1))}
                  >
                    Previous
                  </button>
                  <span className="muted small">
                    Page {listPage} of {Math.max(1, Math.ceil(listTotal / pageSize))}
                  </span>
                  <button
                    type="button"
                    className="secondary sm"
                    disabled={busy || listPage >= Math.ceil(listTotal / pageSize)}
                    onClick={() => setListPage((p) => p + 1)}
                  >
                    Next
                  </button>
                </>
              ) : null}
              <label className="muted small crm-page-size-label">
                Per page
                <select
                  className="crm-page-size-select"
                  aria-label="Rows per page"
                  value={pageSize}
                  disabled={busy}
                  onChange={(e) => {
                    const n = clampPageSize(Number(e.target.value))
                    setPageSize(n)
                    setListPage(1)
                  }}
                >
                  {[25, 50, 100, 200].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </div>
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
                    lastDetailFetchKey.current = null
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
                    lastDetailFetchKey.current = null
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
