import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch, getToken } from '../api/client'

const COL_ORDER = [
  'do_import',
  'input',
  'domain',
  'exists',
  'exists_source',
  'classification',
  'is_parent_company',
  'parent_name',
  'parent_suggested',
  'match_reason',
  'match_method',
  'match_score',
  'evidence',
  'new_parent_candidate',
  'requires_review',
  'decision_reason',
  'decision_confidence',
  'ownership_rationale',
  'needs_review',
  'parent_confirmed',
  'status',
] as const

const COL_LABEL: Record<string, string> = {
  do_import: 'Import',
  input: 'Input',
  domain: 'Domain',
  exists: 'Exists',
  exists_source: 'Exists src',
  classification: 'Class',
  is_parent_company: 'New parent',
  parent_name: 'AI parent',
  parent_suggested: 'DB match',
  match_reason: 'Match',
  match_method: 'Method',
  match_score: 'Score',
  evidence: 'Evidence',
  new_parent_candidate: 'New parent cand.',
  requires_review: 'Needs review',
  decision_reason: 'Decision',
  decision_confidence: 'Confidence',
  ownership_rationale: 'Rationale',
  needs_review: 'Model review',
  parent_confirmed: 'Parent ✓',
  status: 'Status',
}

const READONLY = new Set([
  'exists',
  'exists_source',
  'is_parent_company',
  'parent_name',
  'parent_suggested',
  'match_reason',
  'match_method',
  'match_score',
  'evidence',
  'new_parent_candidate',
  'requires_review',
  'decision_reason',
  'decision_confidence',
  'ownership_rationale',
  'needs_review',
  'status',
])

function fmtCell(v: unknown) {
  if (v == null) return ''
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  return String(v)
}

export default function BulkImportPage() {
  const [text, setText] = useState('')
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null)
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [allowNewParents, setAllowNewParents] = useState(false)
  const [verifyOwnership, setVerifyOwnership] = useState(true)
  const [lastCreated, setLastCreated] = useState<unknown[]>([])
  const [showAllColumns, setShowAllColumns] = useState(false)
  const [doImportAllState, setDoImportAllState] = useState<'all' | 'some' | 'none'>('none')
  const [campaigns, setCampaigns] = useState<Array<{ id: number; name: string; status?: string }>>([])
  const [campaignName, setCampaignName] = useState('')
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | ''>('')
  const [campaignBusy, setCampaignBusy] = useState(false)
  const [showOnlyAmbiguous, setShowOnlyAmbiguous] = useState(false)
  const [commitSummary, setCommitSummary] = useState<Record<string, unknown> | null>(null)
  const [qualityBaseline, setQualityBaseline] = useState<Record<string, unknown> | null>(null)
  const doImportHeaderRef = useRef<HTMLInputElement | null>(null)

  const existingParents = useMemo(() => {
    const ex = preview?.existing_parents
    return Array.isArray(ex) ? (ex as string[]).filter(Boolean) : []
  }, [preview])

  const parentOptions = useMemo(() => {
    const newNames = rows
      .filter((r) => r.is_parent_company && String(r.parent_name || '').trim())
      .map((r) => String(r.parent_name).trim())
    const set = new Set<string>(['', ...existingParents])
    for (const n of newNames) {
      if (n && !existingParents.includes(n)) set.add(n)
    }
    return [...set]
  }, [rows, existingParents])

  const columns = useMemo((): string[] => {
    if (!rows.length) return []
    const keys = new Set<string>()
    for (const r of rows) {
      Object.keys(r).forEach((k) => keys.add(k))
    }
    const ordered: string[] = COL_ORDER.filter((c) => keys.has(c))
    for (const k of keys) {
      if (!ordered.includes(k)) ordered.push(k)
    }
    return ordered
  }, [rows])

  const visibleColumns = useMemo(() => {
    if (!columns.length) return []
    if (showAllColumns) return columns
    const CORE = new Set<string>([
      'do_import',
      'input',
      'domain',
      'exists',
      'exists_source',
      'classification',
      'is_parent_company',
      'parent_confirmed',
      'match_reason',
      'status',
    ])
    return columns.filter((c) => CORE.has(c))
  }, [columns, showAllColumns])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const rows = await apiFetch<Array<{ id: number; name: string; status?: string }>>('/api/campaigns', { silent: true })
        if (!cancelled) setCampaigns(rows || [])
      } catch {
        if (!cancelled) setCampaigns([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const baseline = await apiFetch<Record<string, unknown>>('/api/bulk/judgment-baseline?days=28', { silent: true })
        if (!cancelled) setQualityBaseline(baseline || null)
      } catch {
        if (!cancelled) setQualityBaseline(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!rows.length) {
      setDoImportAllState('none')
      if (doImportHeaderRef.current) doImportHeaderRef.current.indeterminate = false
      return
    }
    const any = rows.some((r) => Boolean(r.do_import))
    const all = rows.every((r) => Boolean(r.do_import))
    const next = all ? 'all' : any ? 'some' : 'none'
    setDoImportAllState(next)
    if (doImportHeaderRef.current) doImportHeaderRef.current.indeterminate = next === 'some'
  }, [rows])

  const doImportSelectedCount = useMemo(() => {
    return rows.reduce((acc, r) => acc + (Boolean(r.do_import) ? 1 : 0), 0)
  }, [rows])

  function setDoImportForAll(next: boolean) {
    setRows((prev) => prev.map((r) => ({ ...r, do_import: next })))
  }

  async function analyzeText(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      const fd = new FormData()
      fd.set('text', text)
      fd.set('verify_ownership', verifyOwnership ? 'true' : 'false')
      const res = await fetch('/api/bulk/analyze-text', {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken() || ''}` },
        body: fd,
      })
      if (!res.ok) throw new Error('Analyze failed')
      const j = await res.json()
      setPreview(j)
      setRows((j.rows as Record<string, unknown>[]) || [])
      setQualityBaseline((j.quality_baseline as Record<string, unknown>) || qualityBaseline)
      setCommitSummary(null)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  async function analyzeFile(file: File | null) {
    if (!file) return
    setBusy(true)
    setErr('')
    try {
      const fd = new FormData()
      fd.set('file', file)
      fd.set('verify_ownership', verifyOwnership ? 'true' : 'false')
      const res = await fetch('/api/bulk/analyze-file', {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken() || ''}` },
        body: fd,
      })
      if (!res.ok) throw new Error('Analyze failed')
      const j = await res.json()
      setPreview(j)
      setRows((j.rows as Record<string, unknown>[]) || [])
      setQualityBaseline((j.quality_baseline as Record<string, unknown>) || qualityBaseline)
      setCommitSummary(null)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  async function analyzeCampaignFile(file: File | null) {
    if (!selectedCampaignId) {
      setErr('Select or create a campaign first, then upload campaign sheet.')
      return
    }
    await analyzeFile(file)
  }

  async function commit() {
    setBusy(true)
    setErr('')
    try {
      const summary = await apiFetch<Record<string, unknown>>('/api/bulk/commit', {
        method: 'POST',
        silent: true,
        json: {
          rows,
          allow_new_parents: allowNewParents,
          campaign_id: selectedCampaignId || null,
        },
      })
      setLastCreated((summary.created as unknown[]) || [])
      setCommitSummary(summary)
      setPreview(null)
      setRows([])
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Commit failed')
    } finally {
      setBusy(false)
    }
  }

  async function undoLast() {
    if (!lastCreated.length) return
    setBusy(true)
    setErr('')
    try {
      await apiFetch('/api/bulk/undo', {
        method: 'POST',
        silent: true,
        json: { created: lastCreated },
      })
      setLastCreated([])
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Undo failed')
    } finally {
      setBusy(false)
    }
  }

  function patchRow(i: number, key: string, value: unknown) {
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, [key]: value } : r)))
  }

  const displayRows = useMemo(() => {
    const indexed = rows.map((row, index) => ({ row, index }))
    if (!showOnlyAmbiguous) return indexed
    return indexed.filter((x) => Boolean(x.row.requires_review))
  }, [rows, showOnlyAmbiguous])

  async function createCampaign() {
    const nm = campaignName.trim()
    if (!nm || campaignBusy) return
    setCampaignBusy(true)
    setErr('')
    try {
      const out = await apiFetch<{ id: number; name: string; status?: string }>('/api/campaigns', {
        method: 'POST',
        json: { name: nm, status: 'active' },
      })
      setCampaigns((prev) => {
        const next = [...prev]
        if (!next.some((x) => x.id === out.id)) next.unshift(out)
        return next
      })
      setSelectedCampaignId(out.id)
      setCampaignName('')
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Could not create campaign')
    } finally {
      setCampaignBusy(false)
    }
  }

  return (
    <div className="page-wide bulk-import-page">
      <header className="intel-hero strategist-header">
        <div className="intel-hero-text">
          <p className="intel-eyebrow">Operations</p>
          <h1>Bulk domain import</h1>
        </div>
      </header>
      <div className="panel">
        <h2>Campaigns (separate from import)</h2>
        <label className="crm-add-field bulk-campaign-select">
          <span>Select campaign</span>
          <select
            className="lh2-dt-input wide"
            value={selectedCampaignId}
            onChange={(e) => setSelectedCampaignId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">None</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <div className="bulk-campaign-inline-create">
          <input
            className="lh2-dt-input wide"
            type="text"
            value={campaignName}
            onChange={(e) => setCampaignName(e.target.value)}
            placeholder="+ Add campaign name"
            autoComplete="off"
          />
          <button type="button" className="secondary sm" onClick={() => void createCampaign()} disabled={campaignBusy || !campaignName.trim()}>
            {campaignBusy ? 'Adding…' : '+ Add campaign'}
          </button>
        </div>
        {campaigns.length ? (
          <p className="muted small bulk-campaign-existing">
            Existing: {campaigns.slice(0, 6).map((c) => c.name).join(', ')}
          </p>
        ) : null}
        <div className="bulk-campaign-upload">
          <h2>Upload campaign sheet (XLSX/CSV)</h2>
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            title="Upload campaign sheet"
            aria-label="Upload campaign sheet"
            onChange={(e) => void analyzeCampaignFile(e.target.files?.[0] || null)}
          />
        </div>
      </div>
      {err && <p className="error">{err}</p>}
      {qualityBaseline && (
        <div className="panel">
          <h2>28-day quality baseline</h2>
          <p className="muted small">
            Imported: {fmtCell(qualityBaseline.imported_rows)} | Reclassifications: {fmtCell(qualityBaseline.reclassified_rows)} | Review rate:{' '}
            {fmtCell(qualityBaseline.review_rate)} | Override rate: {fmtCell(qualityBaseline.override_rate)}
          </p>
        </div>
      )}
      <form onSubmit={analyzeText} className="panel">
        <h2>Input</h2>
        <textarea
          rows={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Domains or names, one per line"
          className="bulk-import-textarea"
          aria-label="Bulk import input domains or names"
        />
        <label className="chk-inline bulk-verify">
          <input
            type="checkbox"
            checked={verifyOwnership}
            onChange={(e) => setVerifyOwnership(e.target.checked)}
          />
          Verify media-house ownership (slower)
        </label>
        <div className="btn-row">
          <button type="submit" className="secondary" disabled={busy}>
            Analyze text
          </button>
        </div>
      </form>
      <div className="panel">
        <h2>File</h2>
        <input
          type="file"
          accept=".csv,.xlsx,.xls"
          title="Upload CSV/XLSX/XLS"
          aria-label="Upload CSV/XLSX/XLS"
          onChange={(e) => analyzeFile(e.target.files?.[0] || null)}
        />
      </div>
      <div className="panel">
        <label className="chk-inline">
          <input type="checkbox" checked={allowNewParents} onChange={(e) => setAllowNewParents(e.target.checked)} />
          Allow new Media House parents on commit
        </label>
      </div>
      {lastCreated.length > 0 && (
        <div className="btn-row">
          <button type="button" className="ghost sm" disabled={busy} onClick={undoLast}>
            Undo last commit
          </button>
        </div>
      )}
      {commitSummary && (
        <div className="panel">
          <h2>Commit outcome</h2>
          <p className="muted small">
            Added independent: {Array.isArray(commitSummary.added_independent) ? (commitSummary.added_independent as unknown[]).length : 0} | Added media
            house: {Array.isArray(commitSummary.added_media_house) ? (commitSummary.added_media_house as unknown[]).length : 0} | Flagged for review:{' '}
            {Array.isArray(commitSummary.flagged_for_review) ? (commitSummary.flagged_for_review as unknown[]).length : 0} | Errors:{' '}
            {Array.isArray(commitSummary.errors) ? (commitSummary.errors as unknown[]).length : 0}
          </p>
        </div>
      )}
      {Array.isArray(preview?.errors) && (preview!.errors as string[]).length > 0 && (
        <ul className="error">
          {(preview!.errors as string[]).map((x, i) => (
            <li key={i}>{x}</li>
          ))}
        </ul>
      )}
      {rows.length > 0 && (
        <>
          <section className="panel">
            <div className="intel-card-head">
              <h2>Review ({displayRows.length}/{rows.length} rows)</h2>
              <div className="bulk-review-right">
                <span className="muted small">
                  {doImportSelectedCount}/{rows.length} selected for import
                </span>
                <label className="chk-inline">
                  <input type="checkbox" checked={showOnlyAmbiguous} onChange={(e) => setShowOnlyAmbiguous(e.target.checked)} />
                  Show only ambiguous rows
                </label>
                <button
                  type="button"
                  className="secondary sm"
                  disabled={busy}
                  onClick={() => setShowAllColumns((v) => !v)}
                >
                  {showAllColumns ? 'Show key columns' : 'Show all columns'}
                </button>
                <button type="button" className="primary" disabled={busy} onClick={commit}>
                  {selectedCampaignId ? 'Commit reviewed rows to campaign' : 'Commit reviewed rows'}
                </button>
              </div>
            </div>
            <div className="lh2-table-wrap bulk-import-table-wrap">
              <table className="lh2-data-table bulk-import-table">
                <thead>
                  <tr>
                    {visibleColumns.map((col) => (
                      <th key={col}>
                        {col === 'do_import' ? (
                          <label className="bulk-th-import">
                            <input
                              ref={doImportHeaderRef}
                              type="checkbox"
                              checked={doImportAllState === 'all'}
                              onChange={(e) => setDoImportForAll(e.target.checked)}
                              aria-label="Select all rows for import"
                            />
                            {COL_LABEL[col] || col}
                          </label>
                        ) : (
                          COL_LABEL[col] || col
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {displayRows.map(({ row, index }) => (
                    <tr key={index} className={Boolean(row.requires_review) ? 'bulk-row-review' : ''}>
                      {visibleColumns.map((col) => (
                        <td key={col}>
                          {col === 'do_import' ? (
                            <input
                              type="checkbox"
                              checked={Boolean(row.do_import)}
                              onChange={(e) => patchRow(index, 'do_import', e.target.checked)}
                              aria-label={`Import row ${index + 1}`}
                            />
                          ) : col === 'classification' ? (
                            <select
                              className="lh2-dt-input"
                              value={String(row.classification || 'independent')}
                              onChange={(e) => patchRow(index, 'classification', e.target.value)}
                              aria-label={`Classification ${index + 1}`}
                            >
                              <option value="independent">independent</option>
                              <option value="media_house">media_house</option>
                            </select>
                          ) : col === 'requires_review' ? (
                            <label className="bulk-review-override">
                              <input
                                type="checkbox"
                                checked={Boolean(row.override_review_block)}
                                onChange={(e) => patchRow(index, 'override_review_block', e.target.checked)}
                              />
                              {Boolean(row.requires_review) ? 'Required' : 'Clear'}
                            </label>
                          ) : col === 'parent_confirmed' ? (
                            <select
                              className="lh2-dt-input wide"
                              value={String(row.parent_confirmed ?? '')}
                              onChange={(e) => patchRow(index, 'parent_confirmed', e.target.value || null)}
                              aria-label={`Parent confirmed ${index + 1}`}
                            >
                              {parentOptions.map((p) => (
                                <option key={p || 'empty'} value={p}>
                                  {p || '—'}
                                </option>
                              ))}
                            </select>
                          ) : READONLY.has(col) ? (
                            <span className="lh2-dt-readonly" title={fmtCell(row[col])}>
                              {fmtCell(row[col]) || '—'}
                            </span>
                          ) : (
                            <input
                              className="lh2-dt-input wide"
                              value={fmtCell(row[col])}
                              onChange={(e) => patchRow(index, col, e.target.value)}
                              aria-label={`${col} row ${index + 1}`}
                            />
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
