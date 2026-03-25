import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useMeta } from '../state/MetaContext'

type Row = {
  company: Record<string, unknown>
  contact: { id: number; name?: string; status?: string }
  next_step_date: string
  primary_spoc: Record<string, unknown> | null
  first_overdue_date_str: string | null
}

export default function SpocPage() {
  const meta = useMeta()
  const [q, setQ] = useState('')
  const [rows, setRows] = useState<Row[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    const params = q.trim() ? `?q=${encodeURIComponent(q)}` : ''
    apiFetch<{ rows: Row[] }>(`/api/spoc/rows${params}`)
      .then((r) => setRows(r.rows || []))
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }, [q])

  const labels = meta?.contact_status_labels || {}
  const statuses = meta?.contact_statuses || []

  async function setStatus(contactId: number, status: string) {
    await apiFetch(`/api/pipeline/contacts/${contactId}/status`, { method: 'PATCH', json: { status } })
    const params = q.trim() ? `?q=${encodeURIComponent(q)}` : ''
    apiFetch<{ rows: Row[] }>(`/api/spoc/rows${params}`).then((r) => setRows(r.rows || []))
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
                <select
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
