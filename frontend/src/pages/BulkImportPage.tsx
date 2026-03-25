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
  'evidence',
  'new_parent_candidate',
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
  evidence: 'Evidence',
  new_parent_candidate: 'New parent cand.',
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
  'evidence',
  'new_parent_candidate',
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
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  async function commit() {
    setBusy(true)
    setErr('')
    try {
      const summary = await apiFetch<{ created?: unknown[] }>('/api/bulk/commit', {
        method: 'POST',
        json: { rows, allow_new_parents: allowNewParents },
      })
      setLastCreated(summary.created || [])
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
      await apiFetch('/api/bulk/undo', { method: 'POST', json: { created: lastCreated } })
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

  return (
    <div className="page-wide bulk-import-page">
      <header className="intel-hero strategist-header">
        <div className="intel-hero-text">
          <p className="intel-eyebrow">Operations</p>
          <h1>Bulk domain import</h1>
        </div>
      </header>
      {err && <p className="error">{err}</p>}
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
              <h2>Review ({rows.length} rows)</h2>
              <div className="bulk-review-right">
                <span className="muted small">
                  {doImportSelectedCount}/{rows.length} selected for import
                </span>
                <button
                  type="button"
                  className="secondary sm"
                  disabled={busy}
                  onClick={() => setShowAllColumns((v) => !v)}
                >
                  {showAllColumns ? 'Show key columns' : 'Show all columns'}
                </button>
                <button type="button" className="primary" disabled={busy} onClick={commit}>
                  Commit reviewed rows
                </button>
              </div>
            </div>
            <p className="muted small">
              Edit classification and parent (confirmed). Review is scrollable; table may still require horizontal scroll.
            </p>
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
                  {rows.map((row, ri) => (
                    <tr key={ri}>
                      {visibleColumns.map((col) => (
                        <td key={col}>
                          {col === 'do_import' ? (
                            <input
                              type="checkbox"
                              checked={Boolean(row.do_import)}
                              onChange={(e) => patchRow(ri, 'do_import', e.target.checked)}
                              aria-label={`Import row ${ri + 1}`}
                            />
                          ) : col === 'classification' ? (
                            <select
                              className="lh2-dt-input"
                              value={String(row.classification || 'independent')}
                              onChange={(e) => patchRow(ri, 'classification', e.target.value)}
                              aria-label={`Classification ${ri + 1}`}
                            >
                              <option value="independent">independent</option>
                              <option value="media_house">media_house</option>
                            </select>
                          ) : col === 'parent_confirmed' ? (
                            <select
                              className="lh2-dt-input wide"
                              value={String(row.parent_confirmed ?? '')}
                              onChange={(e) => patchRow(ri, 'parent_confirmed', e.target.value || null)}
                              aria-label={`Parent confirmed ${ri + 1}`}
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
                              onChange={(e) => patchRow(ri, col, e.target.value)}
                              aria-label={`${col} row ${ri + 1}`}
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
