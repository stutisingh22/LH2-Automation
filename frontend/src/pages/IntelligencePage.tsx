import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { apiFetch, getToken } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useIntelligenceCache, type IntelWorkspaceSnapshot } from '../state/IntelligenceCacheContext'
import { ResearchHtmlView } from '../ui/ResearchHtmlView'
import { MarkdownLazy } from '../ui/MarkdownLazy'

type IntelProfile = {
  domain?: string | null
  name?: string | null
  publisher_type?: string | null
  content_type?: string | null
  monthly_visits?: number | null
  research_status?: string | null
  company_workspace_label?: string | null
}

type IntelWorkspace = {
  profile: IntelProfile
  research_full: string
  research_display_html: string
  research_sections?: IntelSection[]
  recommended_offering?: Record<string, unknown> | null
  platform_presence: Record<string, unknown>
  social_from_search: Record<string, unknown>
  platform_presence_analytics: Record<string, unknown>
  notion_notes: unknown[]
  notion_page_id?: string | null
  advisor_canonical: boolean
}

type IntelSection = { title: string; body_html: string }

type SaveResearchResponse = {
  ok?: boolean
  drive_file_id?: string | null
  drive_error?: string | null
}

type DeepResearchProgress = {
  pct: number
  chars: number
  elapsed: number
  remainingPct: number
  hint: string
}

function safeH2Title(t: string) {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function buildMergedHtml(sections: IntelSection[], overrides: Record<number, string>) {
  return sections
    .map((s, i) => {
      const body = overrides[i] !== undefined ? overrides[i]! : s.body_html
      // Must match Streamlit / ui_helpers.try_split_html_at_lh2_markers (stable vs nested <h2> in bodies).
      return `<!--lh2:section:${i}-->\n<h2>${safeH2Title(s.title)}</h2>\n\n${body}`
    })
    .join('\n\n')
}

function fmtVisits(v: unknown) {
  if (v == null || v === '') return '—'
  if (typeof v === 'number') return v.toLocaleString()
  return String(v)
}

function truncateNavTitle(t: string, max = 44) {
  const s = (t || '').trim()
  if (s.length <= max) return s
  return `${s.slice(0, max - 1)}…`
}

function researchFingerprint(w: IntelWorkspace | null): string {
  let t = (w?.research_display_html || w?.research_full || '').trim()
  if (!t && w && Array.isArray(w.research_sections) && w.research_sections.length > 0) {
    t = w.research_sections.map((s) => `${s.title}\0${s.body_html}`).join('\n')
  }
  let h = 2166136261
  for (let i = 0; i < t.length; i++) {
    h ^= t.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return `${t.length}:${h >>> 0}`
}

/** Match ui_helpers.is_html_content + looks_like_html_fragment so tables/HTML are never fed to Markdown. */
function looksLikeResearchHtml(s: string): boolean {
  const t = (s || '').trim()
  if (!t) return false
  const lt = t.replace(/^\s+/, '')
  if (lt.startsWith('<') && (t.includes('</') || t.includes('/>'))) return true
  if (/<\s*(table|thead|tbody|tfoot|tr|td|th)\b/i.test(t)) return true
  if (/<\s*(div|section|article|ul|ol|pre|blockquote)\b/i.test(t) && t.includes('</')) return true
  if (/<\s*p\b[^>]*>/i.test(t) && /<\/p>/i.test(t)) return true
  if (lt.startsWith('<!')) return true
  return /<\/?[a-z][a-z0-9]*\b/i.test(t)
}

/** Use HTML when content has tags; otherwise markdown (session summary / plain research). */
function ResearchDocContent({ source, className = '' }: { source: string; className?: string }) {
  const t = (source || '').trim()
  if (!t) return null
  if (looksLikeResearchHtml(t)) {
    return <ResearchHtmlView html={t} className={className} />
  }
  return (
    <div className={className.trim() || undefined}>
      <MarkdownLazy text={t} />
    </div>
  )
}

export default function IntelligencePage() {
  const { selection, setSelection } = useSelection()
  const intelCache = useIntelligenceCache()
  const [ws, setWs] = useState<IntelWorkspace | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [sections, setSections] = useState<IntelSection[]>([])
  const [sectionsLoading, setSectionsLoading] = useState(false)
  const [saveDriveFileId, setSaveDriveFileId] = useState<string | null>(null)
  const [overrides, setOverrides] = useState<Record<number, string>>({})
  const sectionRefs = useRef<(HTMLDetailsElement | null)[]>([])
  const [openSectionIds, setOpenSectionIds] = useState<Set<number>>(() => new Set([0]))
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [cmd, setCmd] = useState('')
  const [cmdResult, setCmdResult] = useState<{ title: string; content: string } | null>(null)
  const [intelRaw, setIntelRaw] = useState('')
  const [intelOut, setIntelOut] = useState<Record<string, unknown> | null>(null)
  const [drProgress, setDrProgress] = useState<DeepResearchProgress | null>(null)

  const cid = selection.companyId
  const aid = selection.advisorCompanyId
  const src = selection.source
  const sid = selection.sessionId

  const companyPersistKey = useMemo(
    () => `${src}|c:${cid ?? ''}|a:${aid ?? ''}`,
    [src, cid, aid],
  )

  const researchFpRef = useRef('')
  const tabSnapshotRef = useRef({
    ws: null as IntelWorkspace | null,
    sections: [] as IntelSection[],
    overrides: {} as Record<number, string>,
    openSectionIds: [] as number[],
  })

  const loadWorkspace = useCallback(
    async (opts?: { sessionId?: number | null; afterSave?: boolean; silent?: boolean }) => {
      setLoadErr('')
      const q = new URLSearchParams()
      if (src === 'media' && aid != null) q.set('advisor_company_id', String(aid))
      else if (cid != null) q.set('company_id', String(cid))
      const effSid = opts?.sessionId !== undefined ? opts.sessionId : sid
      if (effSid != null) q.set('session_id', String(effSid))
      q.set('compact', 'true')
      try {
        const data = await apiFetch<IntelWorkspace>(
          `/api/intelligence/workspace?${q}`,
          opts?.silent === true ? { silent: true } : {},
        )
        const newFp = researchFingerprint(data)
        if (newFp !== researchFpRef.current) {
          setOverrides({})
          setCmdResult(null)
          setIntelOut(null)
          if (!opts?.afterSave) {
            setOpenSectionIds(new Set([0]))
          }
        }
        researchFpRef.current = newFp
        setWs(data)
        if (Array.isArray(data.research_sections) && data.research_sections.length > 0) {
          setSections(data.research_sections)
          setSectionsLoading(false)
        }
      } catch (e) {
        setWs(null)
        researchFpRef.current = ''
        setLoadErr(e instanceof Error ? e.message : 'Load failed')
      }
    },
    [cid, aid, src, sid],
  )

  useEffect(() => {
    if (!cid && !(src === 'media' && aid != null)) {
      setWs(null)
      setSections([])
      setSaveDriveFileId(null)
      return
    }
    setSaveDriveFileId(null)
    loadWorkspace()
  }, [loadWorkspace, cid, aid, src, sid])

  useLayoutEffect(() => {
    if (!cid && !(src === 'media' && aid != null)) return
    if (!intelCache) return
    const snap = intelCache.getSnapshot(companyPersistKey)
    if (snap?.ws) {
      setWs(snap.ws as IntelWorkspace)
      setSections(snap.sections as IntelSection[])
      setOverrides(snap.overrides)
      setOpenSectionIds(new Set(snap.openSectionIds))
      researchFpRef.current = researchFingerprint(snap.ws as IntelWorkspace)
    } else {
      setWs(null)
      setSections([])
      setOverrides({})
      setOpenSectionIds(new Set([0]))
      researchFpRef.current = ''
    }
  }, [companyPersistKey, intelCache, cid, aid, src])

  useEffect(() => {
    if (!intelCache) return
    const s = tabSnapshotRef.current
    if (s.ws || s.sections.length > 0) {
      intelCache.putSnapshot(companyPersistKey, {
        ws: s.ws as IntelWorkspaceSnapshot,
        sections: s.sections,
        overrides: s.overrides,
        openSectionIds: s.openSectionIds,
      })
    }
  }, [companyPersistKey, intelCache, ws, sections, overrides, openSectionIds])

  const displayForSplit = useMemo(() => {
    const fromWs = (ws?.research_display_html || ws?.research_full || '').trim()
    if (fromWs) return fromWs
    const secs = ws?.research_sections
    if (Array.isArray(secs) && secs.length > 0) {
      return buildMergedHtml(secs as IntelSection[], {})
    }
    return ''
  }, [ws])

  const advisorCanon = Boolean(ws?.advisor_canonical)

  useEffect(() => {
    if (Array.isArray(ws?.research_sections) && ws.research_sections.length > 0) {
      setSections(ws.research_sections)
      setSectionsLoading(false)
      return
    }
    if (!displayForSplit) {
      setSections([])
      setSectionsLoading(false)
      return
    }
    const ac = new AbortController()
    setSectionsLoading(true)
    apiFetch<{ sections: IntelSection[] }>('/api/intelligence/research-sections', {
      method: 'POST',
      silent: true,
      json: { display: displayForSplit, advisor_canonical: advisorCanon },
      signal: ac.signal,
    })
      .then((r) => {
        const incoming = Array.isArray(r.sections) ? r.sections : []
        setSections(incoming)
      })
      .catch((e: unknown) => {
        const name = e && typeof e === 'object' && 'name' in e ? String((e as Error).name) : ''
        if (name === 'AbortError') return
        setSections([])
      })
      .finally(() => {
        if (!ac.signal.aborted) setSectionsLoading(false)
      })
    return () => ac.abort()
  }, [displayForSplit, advisorCanon, ws?.research_sections])

  function jumpToSection(i: number) {
    setOpenSectionIds((prev) => {
      const n = new Set(prev)
      n.add(i)
      return n
    })
    requestAnimationFrame(() => {
      const el = sectionRefs.current[i]
      if (!el) return
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }

  const offering = ws?.recommended_offering as Record<string, unknown> | null | undefined
  const pa = ws?.platform_presence_analytics || {}
  const sw = (pa.similarweb || {}) as Record<string, unknown>
  const pp = (ws?.platform_presence || {}) as Record<string, boolean | string>

  async function runDeep() {
    if (!ws) return
    setBusy('dr')
    setErr('')
    setDrProgress({
      pct: 0,
      chars: 0,
      elapsed: 0,
      remainingPct: 100,
      hint: 'Starting — connecting to the research model…',
    })
    const body =
      src === 'media' && aid != null
        ? { advisor_company_id: aid, contact_id: selection.contactId, session_id: sid }
        : { company_id: cid, contact_id: selection.contactId, session_id: sid }
    const token = getToken()
    try {
      const res = await fetch('/api/intelligence/deep-research-stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        let detail = text.trim() || res.statusText
        try {
          const j = JSON.parse(text) as { detail?: unknown }
          if (j?.detail != null)
            detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
        } catch {
          /* not JSON */
        }
        throw new Error(detail.slice(0, 500) || `HTTP ${res.status}`)
      }
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No response body')
      const dec = new TextDecoder()
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        for (;;) {
          const sep = buf.indexOf('\n\n')
          if (sep < 0) break
          const raw = buf.slice(0, sep)
          buf = buf.slice(sep + 2)
          const dataLine = raw
            .split('\n')
            .map((l) => l.trimEnd())
            .find((l) => l.startsWith('data:'))
          if (!dataLine) continue
          const jsonStr = dataLine.slice(5).trim()
          if (!jsonStr) continue
          let msg: Record<string, unknown>
          try {
            msg = JSON.parse(jsonStr) as Record<string, unknown>
          } catch {
            continue
          }
          const typ = msg.type as string
          if (typ === 'start') {
            setDrProgress((prev) =>
              prev
                ? {
                    ...prev,
                    hint: 'Model is running (web search + long write). Progress reflects output length, not time.',
                  }
                : null,
            )
          } else if (typ === 'progress') {
            const pct = Number(msg.pct) || 0
            setDrProgress({
              pct,
              chars: Number(msg.chars) || 0,
              elapsed: Number(msg.elapsed) || 0,
              remainingPct:
                typeof msg.remaining_pct === 'number'
                  ? msg.remaining_pct
                  : Math.max(0, 100 - pct),
              hint: 'Receiving research text…',
            })
          } else if (typ === 'pulse') {
            const elapsed = Number(msg.elapsed) || 0
            setDrProgress((prev) =>
              prev
                ? {
                    ...prev,
                    elapsed,
                    remainingPct: Math.max(0, 100 - prev.pct),
                    hint: 'Still working — web search or tool steps may pause visible output for stretches.',
                  }
                : null,
            )
          } else if (typ === 'error') {
            throw new Error(String(msg.detail || 'Deep research failed'))
          } else if (typ === 'complete') {
            const rawSid = Number(msg.session_id)
            const sessionIdDone = Number.isFinite(rawSid) ? rawSid : null
            setDrProgress((prev) =>
              prev
                ? {
                    pct: 100,
                    chars: Number(msg.chars) || prev.chars,
                    elapsed: prev.elapsed,
                    remainingPct: 0,
                    hint: 'Saving and loading workspace…',
                  }
                : null,
            )
            if (sessionIdDone != null) setSelection({ sessionId: sessionIdDone })
            setOverrides({})
            setOpenSectionIds(new Set([0]))
            await loadWorkspace(
              sessionIdDone != null ? { sessionId: sessionIdDone, silent: true } : { silent: true },
            )
          }
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Deep research failed')
    } finally {
      setBusy('')
      setDrProgress(null)
    }
  }

  async function runOffering() {
    setBusy('off')
    setErr('')
    try {
      const r = await apiFetch<Record<string, unknown>>('/api/intelligence/offering-recommendation', {
        method: 'POST',
        silent: true,
        json: {
          company_id: src === 'independent' ? cid : null,
          advisor_company_id: src === 'media' ? aid : null,
          research_text: ws?.research_full || '',
          session_id: sid,
        },
      })
      const pk = (r.product_key as string | undefined)?.toLowerCase()
      if (pk === 'mediaos' || pk === 'accelerate') {
        setWs((prev) =>
          prev
            ? {
                ...prev,
                recommended_offering: {
                  product_key: pk,
                  rationale: String(r.rationale || ''),
                  updated_at: String(r.updated_at || new Date().toISOString()),
                },
              }
            : prev,
        )
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Offering failed')
    } finally {
      setBusy('')
    }
  }

  async function saveResearch() {
    if (!ws) return
    const sectionsForSave =
      sections.length > 0
        ? sections.map((s, i) => ({
            title: s.title,
            body_html: overrides[i] !== undefined ? overrides[i]! : s.body_html,
          }))
        : []
    const merged = sectionsForSave.length > 0 ? buildMergedHtml(sections, overrides) : displayForSplit
    if (!merged.trim()) {
      setErr('Nothing to save')
      return
    }
    setBusy('save')
    setErr('')
    setSaveDriveFileId(null)
    try {
      const out = await apiFetch<SaveResearchResponse>('/api/intelligence/save-research', {
        method: 'POST',
        silent: true,
        json: {
          company_id: src === 'independent' ? cid : null,
          advisor_company_id: src === 'media' ? aid : null,
          session_id: sid,
          merged_html: merged,
          sections: sectionsForSave,
        },
      })
      if (sectionsForSave.length > 0) {
        setWs((prev) =>
          prev
            ? {
                ...prev,
                research_full: merged,
                research_display_html: merged,
                research_sections: sectionsForSave,
              }
            : prev,
        )
        setSections(sectionsForSave)
      }
      await loadWorkspace(
        sid != null
          ? { sessionId: sid, afterSave: true, silent: true }
          : { afterSave: true, silent: true },
      )
      setEditingIdx(null)
      const derr = (out.drive_error && String(out.drive_error).trim()) || ''
      const fid = out.drive_file_id != null && String(out.drive_file_id).trim()
        ? String(out.drive_file_id).trim()
        : ''
      if (derr) {
        setErr(
          `Saved to the database. Google Drive did not update: ${derr}`,
        )
      } else if (fid) {
        setSaveDriveFileId(fid)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy('')
    }
  }

  async function deleteResearch() {
    if (!confirm('Delete deep research from Command Center for this company?')) return
    setBusy('del')
    setErr('')
    try {
      await apiFetch('/api/intelligence/delete-research', {
        method: 'POST',
        silent: true,
        json: {
          company_id: src === 'independent' ? cid : null,
          advisor_company_id: src === 'media' ? aid : null,
          session_id: sid,
        },
      })
      // Remove all cached snapshots for this company/advisor across session ids,
      // so old research cannot reappear after route changes.
      const cachePrefix = `${src}|c:${cid ?? ''}|a:${aid ?? ''}`
      intelCache?.clearByPrefix(cachePrefix)
      setWs((prev) =>
        prev
          ? {
              ...prev,
              research_full: '',
              research_display_html: '',
            }
          : prev,
      )
      researchFpRef.current = ''
      if (sid != null) setSelection({ sessionId: null })
      await loadWorkspace({ sessionId: null, silent: true })
      setSections([])
      setOverrides({})
      setOpenSectionIds(new Set([0]))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBusy('')
    }
  }

  async function refreshAnalytics() {
    setBusy('pa')
    setErr('')
    try {
      const r = await apiFetch<{ analytics: Record<string, unknown> }>(
        '/api/intelligence/refresh-platform-analytics',
        {
          method: 'POST',
          silent: true,
          json: {
            company_id: src === 'independent' ? cid : null,
            advisor_company_id: src === 'media' ? aid : null,
          },
        },
      )
      setWs((prev) =>
        prev ? { ...prev, platform_presence_analytics: r.analytics || {} } : prev,
      )
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Analytics refresh failed')
    } finally {
      setBusy('')
    }
  }

  async function runResearchCommand() {
    if (!cmd.trim()) return
    setBusy('cmd')
    setErr('')
    try {
      const r = await apiFetch<{ title: string; content: string }>('/api/intelligence/research-command', {
        method: 'POST',
        silent: true,
        json: {
          company_id: src === 'independent' ? cid : null,
          advisor_company_id: src === 'media' ? aid : null,
          current_text: displayForSplit,
          command: cmd.trim(),
        },
      })
      setCmdResult(r)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Command failed')
    } finally {
      setBusy('')
    }
  }

  async function appendCommandToResearch() {
    if (!cmdResult?.content || !ws) return
    setBusy('ap')
    setErr('')
    try {
      const r = await apiFetch<{ merged_html: string; research_display_html: string }>(
        '/api/intelligence/research-command-append',
        {
          method: 'POST',
          silent: true,
          json: {
            company_id: src === 'independent' ? cid : null,
            advisor_company_id: src === 'media' ? aid : null,
            session_id: sid,
            current_merged_html: buildMergedHtml(sections, overrides) || displayForSplit,
            new_content: cmdResult.content,
            title: cmdResult.title || 'Extended research',
          },
        },
      )
      setWs((prev) =>
        prev
          ? {
              ...prev,
              research_full: r.merged_html,
              research_display_html: r.research_display_html || r.merged_html,
            }
          : prev,
      )
      setCmdResult(null)
      setCmd('')
      await loadWorkspace({ silent: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Append failed')
    } finally {
      setBusy('')
    }
  }

  async function runIntelRequest() {
    if (!intelRaw.trim()) return
    setBusy('ir')
    setErr('')
    try {
      const r = await apiFetch<Record<string, unknown>>('/api/intelligence/intel-request', {
        method: 'POST',
        silent: true,
        json: {
          company_id: src === 'independent' ? cid : null,
          advisor_company_id: src === 'media' ? aid : null,
          raw: intelRaw.trim(),
        },
      })
      setIntelOut(r)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy('')
    }
  }

  function startEdit(i: number) {
    const s = sections[i]
    if (!s) return
    setEditingIdx(i)
    setEditDraft(overrides[i] !== undefined ? overrides[i]! : s.body_html)
  }

  function applyEdit() {
    if (editingIdx == null) return
    setOverrides((o) => ({ ...o, [editingIdx]: editDraft }))
    setEditingIdx(null)
  }

  tabSnapshotRef.current = {
    ws,
    sections,
    overrides,
    openSectionIds: [...openSectionIds],
  }

  if (!cid && !(src === 'media' && aid != null)) {
    return (
      <div className="empty-state">
        <p>Select a company (Independent or Media Houses) first.</p>
      </div>
    )
  }

  if (loadErr && !ws) {
    return (
      <div className="empty-state">
        <p className="error">{loadErr}</p>
      </div>
    )
  }

  const p = ws?.profile
  const hasResearch = Boolean(displayForSplit)

  return (
    <div className="page-wide intel-page">
      {drProgress && (
        <div className="intel-dr-overlay" role="status" aria-live="polite" aria-busy="true">
          <div className="intel-dr-card">
            <p className="intel-dr-eyebrow">Deep research</p>
            <h2 className="intel-dr-title">Generating institutional research</h2>
            <div className="intel-dr-bar-track" aria-hidden>
              <div className="intel-dr-bar-fill" style={{ width: `${drProgress.pct}%` }} />
            </div>
            <p className="intel-dr-primary">
              <strong>{drProgress.pct}%</strong> complete
              <span className="intel-dr-sep">·</span>
              <span className="muted">~{drProgress.remainingPct}% remaining</span>
              <span className="intel-dr-sep">·</span>
              <span className="muted">{drProgress.chars.toLocaleString()} chars</span>
              <span className="intel-dr-sep">·</span>
              <span className="muted">{drProgress.elapsed}s</span>
            </p>
            <p className="intel-dr-hint muted small">{drProgress.hint}</p>
          </div>
        </div>
      )}
      <header className="intel-hero">
        <div className="intel-hero-text">
          <p className="intel-eyebrow">Intelligence</p>
          <h1>
            {(p?.company_workspace_label || '').trim() || p?.name || p?.domain || 'Company'}
          </h1>
          {!(p?.company_workspace_label || '').trim() ? (
            <p className="intel-domain muted">{p?.domain || '—'}</p>
          ) : null}
        </div>
        <div className="intel-hero-actions">
          <button type="button" className="primary sm" disabled={!!busy} onClick={runDeep}>
            {busy === 'dr' ? 'Running…' : hasResearch ? 'Re-run deep research' : 'Run deep research'}
          </button>
          <button type="button" className="secondary sm" disabled={!!busy || !hasResearch} onClick={deleteResearch}>
            Delete research
          </button>
          <button type="button" className="ghost sm" disabled={!!busy} onClick={() => void loadWorkspace()}>
            Refresh
          </button>
        </div>
      </header>

      {(err || loadErr) && <p className="error">{err || loadErr}</p>}
      {saveDriveFileId && !err && (
        <p className="muted small">
          Saved to the database.{' '}
          <a
            href={`https://docs.google.com/document/d/${saveDriveFileId}/edit`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open Google Doc
          </a>
        </p>
      )}

      <section className="intel-kpis">
        <div className="intel-kpi">
          <span className="intel-kpi-label">Publisher</span>
          <span className="intel-kpi-value">{p?.publisher_type || '—'}</span>
        </div>
        <div className="intel-kpi">
          <span className="intel-kpi-label">Content</span>
          <span className="intel-kpi-value">{p?.content_type || '—'}</span>
        </div>
        <div className="intel-kpi">
          <span className="intel-kpi-label">Visits</span>
          <span className="intel-kpi-value">{fmtVisits(p?.monthly_visits)}</span>
        </div>
        <div className="intel-kpi">
          <span className="intel-kpi-label">Research status</span>
          <span className="intel-kpi-value">{p?.research_status || '—'}</span>
        </div>
      </section>

      <section className="panel intel-card offering-card">
        <div className="intel-card-head">
          <h2>Offering recommendation</h2>
          <button type="button" className="secondary sm" disabled={!!busy} onClick={runOffering}>
            {busy === 'off' ? '…' : offering?.product_key ? 'Refresh' : 'Get recommendation'}
          </button>
        </div>
        {offering?.product_key ? (
          <div className="offering-pill">
            <span className="offering-key">{String(offering.product_key)}</span>
            <p className="offering-rationale">{String(offering.rationale || '')}</p>
          </div>
        ) : null}
      </section>

      <section className="panel intel-card">
        <div className="intel-card-head">
          <h2>Platform presence &amp; analytics</h2>
          <button type="button" className="secondary sm" disabled={busy === 'pa'} onClick={refreshAnalytics}>
            {busy === 'pa' ? '…' : 'Refresh analytics'}
          </button>
        </div>
        <div className="intel-presence-grid">
          {(['msn', 'yahoo', 'newsbreak'] as const).map((k) => (
            <div key={k} className={`presence-chip ${pp[k] ? 'on' : 'off'}`}>
              <span className="presence-name">{k.toUpperCase()}</span>
              <span className="presence-state">{pp[k] ? 'Yes' : 'No'}</span>
            </div>
          ))}
        </div>
        <div className="intel-metrics-row">
          <div className="intel-metric">
            <span className="intel-metric-label">Analytics status</span>
            <span className="intel-metric-value">{String(pa.status || '—')}</span>
          </div>
          <div className="intel-metric">
            <span className="intel-metric-label">Monthly visits</span>
            <span className="intel-metric-value">
              {typeof sw.latest_monthly_visits === 'number'
                ? sw.latest_monthly_visits.toLocaleString()
                : 'n/a'}
            </span>
          </div>
          <div className="intel-metric">
            <span className="intel-metric-label">Updated</span>
            <span className="intel-metric-value muted small">{String(pa.updated_at || '—')}</span>
          </div>
        </div>
      </section>

      <section className="panel intel-card">
        <div className="intel-card-head">
          <h2>Research document</h2>
          {sections.length > 0 && (
            <button type="button" className="primary sm" disabled={!!busy} onClick={saveResearch}>
              {busy === 'save' ? 'Saving…' : 'Save to DB & Drive'}
            </button>
          )}
        </div>
        {!hasResearch ? (
          <p className="muted">
            No research yet. Run <strong>deep research</strong> above, then edit sections and save (DB + Drive).
          </p>
        ) : sectionsLoading && sections.length === 0 ? (
          <p className="muted intel-sections-loading">Loading research sections…</p>
        ) : sections.length === 0 ? (
          <ResearchDocContent source={displayForSplit} className="research-html intel-fallback-html" />
        ) : (
          <>
            <nav className="intel-section-nav" aria-label="Research sections">
              {sections.map((sec, i) => (
                <button
                  key={`nav-${i}-${sec.title}`}
                  type="button"
                  className="intel-section-nav-pill"
                  onClick={() => jumpToSection(i)}
                >
                  <span className="intel-section-nav-idx">{i + 1}</span>
                  {truncateNavTitle(sec.title)}
                </button>
              ))}
            </nav>
            <div className="intel-accordion">
            {sections.map((sec, i) => (
              <details
                key={`${sec.title}-${i}`}
                id={`intel-sec-${i}`}
                ref={(el) => {
                  sectionRefs.current[i] = el
                }}
                className="intel-acc-item"
                open={openSectionIds.has(i)}
                onToggle={(e) => {
                  const det = e.currentTarget
                  const idx = i
                  setOpenSectionIds((prev) => {
                    const n = new Set(prev)
                    if (det.open) n.add(idx)
                    else n.delete(idx)
                    return n
                  })
                }}
              >
                <summary>{sec.title}</summary>
                <div className="intel-acc-body">
                  {editingIdx === i ? (
                    <>
                      <textarea
                        className="intel-edit-ta"
                        rows={14}
                        value={editDraft}
                        onChange={(e) => setEditDraft(e.target.value)}
                      />
                      <div className="btn-row">
                        <button type="button" className="primary sm" onClick={applyEdit}>
                          Apply section edit
                        </button>
                        <button type="button" className="ghost sm" onClick={() => setEditingIdx(null)}>
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <ResearchHtmlView
                        html={overrides[i] !== undefined ? overrides[i]! : sec.body_html}
                        className="research-html intel-section-html"
                      />
                      <button type="button" className="secondary sm" onClick={() => startEdit(i)}>
                        Edit section
                      </button>
                    </>
                  )}
                </div>
              </details>
            ))}
            </div>
          </>
        )}
      </section>

      <section className="panel intel-card">
        <h2>Deep research command</h2>
        <div className="intel-cmd-row">
          <input
            type="text"
            className="intel-cmd-input"
            placeholder="e.g. Add competitor landscape, expand power mapping…"
            value={cmd}
            onChange={(e) => setCmd(e.target.value)}
          />
          <button type="button" className="primary sm" disabled={busy === 'cmd'} onClick={runResearchCommand}>
            Run
          </button>
        </div>
        {cmdResult && (
          <div className="intel-cmd-result">
            <h3>{cmdResult.title}</h3>
            <pre className="intel-cmd-pre">{cmdResult.content}</pre>
            <div className="btn-row">
              <button type="button" className="primary sm" disabled={busy === 'ap'} onClick={appendCommandToResearch}>
                {busy === 'ap' ? '…' : 'Add to research & save'}
              </button>
              <button type="button" className="ghost sm" onClick={() => setCmdResult(null)}>
                Dismiss
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="panel intel-card">
        <h2>Person lookup &amp; suggestions</h2>
        <div className="intel-lookup-stack">
          <textarea
            className="intel-lookup-textarea"
            rows={3}
            value={intelRaw}
            onChange={(e) => setIntelRaw(e.target.value)}
            placeholder="linkedin.com/in/… or email or ‘expand section on…’"
          />
          <div className="intel-lookup-actions">
            <button type="button" className="secondary sm" disabled={busy === 'ir'} onClick={runIntelRequest}>
              {busy === 'ir' ? '…' : 'Run'}
            </button>
          </div>
        </div>
        {intelOut && (
          <div className="intel-intel-out">
            {intelOut.kind === 'person' && (
              <>
                <h3>Person research</h3>
                <pre className="intel-cmd-pre">{String(intelOut.brief || '')}</pre>
              </>
            )}
            {intelOut.kind === 'suggestion' && (
              <>
                <h3>Suggestion</h3>
                <pre className="intel-cmd-pre">{String(intelOut.suggestion || '')}</pre>
              </>
            )}
            <button type="button" className="ghost sm" onClick={() => setIntelOut(null)}>
              Dismiss
            </button>
          </div>
        )}
      </section>

      {ws && Array.isArray(ws.notion_notes) && ws.notion_notes.length > 0 && (
        <section className="panel intel-card intel-notion-panel">
          <h2>Linked Notion notes</h2>
          <ul className="intel-notion-list">
            {ws.notion_notes.map((n, i) => {
              const note = n as Record<string, unknown>
              const title =
                typeof note.title === 'string' && note.title.trim()
                  ? note.title
                  : `Note ${i + 1}`
              const content =
                typeof note.content === 'string'
                  ? note.content
                  : JSON.stringify(note, null, 2)
              return (
                <li key={`${title}-${i}`} className="intel-notion-card">
                  <details>
                    <summary>{title}</summary>
                    <div className="intel-notion-body">
                      <MarkdownLazy text={content} />
                    </div>
                  </details>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </div>
  )
}
