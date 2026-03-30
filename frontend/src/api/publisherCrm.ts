export type ContactSummary = {
  kind: 'contact' | 'spoc'
  name: string
  role: string
  email: string
  linkedin_url: string
}

export type PlannedPreviewItem = {
  id: number
  sequence_step: number | null
  channel: string
  status: string
  scheduled_at: string | null
  description: string
}

export type ApiPublisherBrand = {
  company_domain: string
  has_research?: boolean
  relevance?: 'relevant' | 'irrelevant' | '-'
  relevance_reason?: string
  status: string
  status_label: string
  next_step_date: string
  due_today: boolean
  monthly_visits: number | null
  syndication: string[]
  syndication_links?: Record<string, string>
  spoc_name: string
  contacts_summary: ContactSummary[]
  advisor_company_id: number | null
  spoc_names?: string[]
}

export type ApiPublisherGroup = {
  key: string
  source: 'independent' | 'media'
  display_title: string
  has_research?: boolean
  relevance?: 'relevant' | 'irrelevant' | '-'
  relevance_reason?: string
  parent_name: string | null
  company_id: number | null
  status: string
  status_label: string
  due_today: boolean
  next_step_date: string
  monthly_visits: number | null
  syndication: string[]
  syndication_links?: Record<string, string>
  spoc_name: string
  spoc_names?: string[]
  ceo_display_name?: string | null
  contacts_summary: ContactSummary[]
  brands?: ApiPublisherBrand[]
  /** Media parent list rows: child count when `brands` omitted from list API. */
  brand_count?: number
  contacted_brand_count?: number
  contact_ids: number[]
  advisor_company_ids: number[]
  planned_preview: PlannedPreviewItem[]
}

function contactSummariesFromSpocNames(names: string[]): ContactSummary[] {
  return names
    .map((n) => (n || '').trim())
    .filter(Boolean)
    .map((name) => ({
      kind: 'spoc' as const,
      name,
      role: '—',
      email: '',
      linkedin_url: '',
    }))
}

/** Normalized group for CRM UIs (camelCase display fields). */
export type UiPublisherGroup = {
  key: string
  source: 'independent' | 'media'
  displayTitle: string
  hasResearch?: boolean
  relevance?: 'relevant' | 'irrelevant' | '-'
  relevance_reason?: string
  parent_name?: string
  company_id?: number
  status: string
  status_label: string
  due_today: boolean
  next_step_date: string
  monthly_visits: number | null
  syndication: string[]
  syndication_links?: Record<string, string>
  spoc_name: string
  contacts_summary: ContactSummary[]
  brands?: UiPublisherBrand[]
  brandCount?: number
  contactedBrandCount?: number
  contact_ids: number[]
  advisor_company_ids: number[]
  planned_preview: PlannedPreviewItem[]
  ceoDisplayName?: string | null
  spoc_names?: string[]
}

export type UiPublisherBrand = {
  company_domain: string
  hasResearch?: boolean
  relevance?: 'relevant' | 'irrelevant' | '-'
  relevance_reason?: string
  status: string
  status_label: string
  next_step_date: string
  due_today: boolean
  monthly_visits: number | null
  syndication: string[]
  syndication_links?: Record<string, string>
  spoc_name: string
  contacts_summary: ContactSummary[]
  advisor_company_id: number | null
  spoc_names?: string[]
}

export function mapApiPublisherGroup(g: ApiPublisherGroup): UiPublisherGroup {
  let contacts = g.contacts_summary || []
  if (!contacts.length && (g.spoc_names || []).length) {
    contacts = contactSummariesFromSpocNames(g.spoc_names || [])
  }
  const mapBrand = (b: ApiPublisherBrand): UiPublisherBrand => {
    let bc = b.contacts_summary || []
    if (!bc.length && (b.spoc_names || []).length) {
      bc = contactSummariesFromSpocNames(b.spoc_names || [])
    }
    return {
      company_domain: b.company_domain,
      hasResearch: Boolean(b.has_research),
      relevance: b.relevance || '-',
      relevance_reason: b.relevance_reason || '',
      status: b.status,
      status_label: b.status_label,
      next_step_date: b.next_step_date,
      due_today: b.due_today,
      monthly_visits: b.monthly_visits,
      syndication: b.syndication,
      syndication_links: b.syndication_links || {},
      spoc_name: b.spoc_name,
      contacts_summary: bc,
      advisor_company_id: b.advisor_company_id,
      spoc_names: b.spoc_names,
    }
  }
  return {
    key: g.key,
    source: g.source,
    displayTitle: g.display_title,
    hasResearch: Boolean(g.has_research),
    relevance: g.relevance || '-',
    relevance_reason: g.relevance_reason || '',
    parent_name: g.parent_name ?? undefined,
    company_id: g.company_id ?? undefined,
    status: g.status,
    status_label: g.status_label,
    due_today: g.due_today,
    next_step_date: g.next_step_date,
    monthly_visits: g.monthly_visits,
    syndication: g.syndication,
    syndication_links: g.syndication_links || {},
    spoc_name: g.spoc_name,
    contacts_summary: contacts,
    brands: g.brands?.map(mapBrand),
    brandCount: g.brand_count,
    contactedBrandCount: g.contacted_brand_count,
    contact_ids: g.contact_ids || [],
    advisor_company_ids: g.advisor_company_ids || [],
    planned_preview: g.planned_preview || [],
    ceoDisplayName: g.ceo_display_name ?? undefined,
    spoc_names: g.spoc_names,
  }
}
