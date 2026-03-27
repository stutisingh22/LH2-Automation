import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useMeta } from '../state/MetaContext'

type Row = {
  company: Record<string, unknown>
  contact: { id: number; name?: string; status?: string }
  next_step_date: string
  primary_spoc: { id?: number; name?: string; email?: string } | null
  first_overdue_date_str: string | null
}

export default function SpocPage() {
  const meta = useMeta()
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [draftEmailBySpocId, setDraftEmailBySpocId] = useState<Record<number, string>>({})
  const [savingSpocId, setSavingSpocId] = useState<number | null>(null)
  const [err, setErr] = useState('')

  function refreshRows(silent = false) {
    const params = q.trim() ? `?q=${encodeURIComponent(q)}` : ''
    apiFetch<{ rows: Row[] }>(`/api/spoc/rows${params}`, { silent })
      .then((r) => {
        const list = r.rows || []
        setRows(list)
        const nextDrafts: Record<number, string> = {}
        for (const row of list) {
          const sid = Number(row.primary_spoc?.id || 0)
          if (sid) nextDrafts[sid] = String(row.primary_spoc?.email || '')
        }
        setDraftEmailBySpocId(nextDrafts)
      })
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }

  useEffect(() => {
    refreshRows()
  }, [q])

  const labels = meta?.contact_status_labels || {}
  const statuses = meta?.contact_statuses || []

  async function setStatus(contactId: number, status: string) {
    await apiFetch(`/api/pipeline/contacts/${contactId}/status`, {
      method: 'PATCH',
      json: { status },
    })
    refreshRows(true)
  }

  async function saveSpocEmail(spocContactId: number) {
    if (!spocContactId) return
    setSavingSpocId(spocContactId)
    setErr('')
    try {
      await apiFetch(`/api/spoc/contacts/${spocContactId}`, {
        method: 'PATCH',
        json: { email: draftEmailBySpocId[spocContactId] || '' },
      })
      refreshRows(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save SPOC email')
    } finally {
      setSavingSpocId(null)
    }
  }

  return (
    <div className="page-wide">
      <h1>SPOC tracking</h1>
      <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      {err && <p className="error">{err}</p>}
      <table className="data-table">
        <thead>
          <tr>
            <th>Company</th>
            <th>Contact</th>
            <th>SPOC email (editable)</th>
            <th>Status</th>
            <th>Next</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.contact.id}>
              <td>{String(r.company.name || r.company.domain)}</td>
              <td>{r.primary_spoc ? String(r.primary_spoc.name) : r.contact.name}</td>
              <td>
                {r.primary_spoc?.id ? (
                  <div className="row">
                    <input
                      className="search"
                      placeholder="spoc@email.com"
                      value={draftEmailBySpocId[r.primary_spoc.id] || ''}
                      onChange={(e) =>
                        setDraftEmailBySpocId((prev) => ({ ...prev, [r.primary_spoc!.id!]: e.target.value }))
                      }
                    />
                    <button
                      type="button"
                      className="sm"
                      disabled={savingSpocId === r.primary_spoc.id}
                      onClick={() => void saveSpocEmail(r.primary_spoc!.id!)}
                    >
                      {savingSpocId === r.primary_spoc.id ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                ) : (
                  <span className="muted">No SPOC row</span>
                )}
              </td>
              <td>
                <select
                  aria-label="Contact status"
                  value={r.contact.status || 'not_contacted'}
                  onChange={(e) => setStatus(r.contact.id, e.target.value)}
                >
                  {statuses.map((s) => (
                    <option key={s} value={s}>
                      {labels[s] || s}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                {r.first_overdue_date_str ? `⚠ ${r.first_overdue_date_str}` : r.next_step_date}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
