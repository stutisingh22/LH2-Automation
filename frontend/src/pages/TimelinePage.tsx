import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'

function timelineDisplayDate(a: Record<string, unknown>): string {
  const sa = a.scheduled_at
  if (sa != null && String(sa).trim()) return String(sa).slice(0, 10)
  return String((a.created_at as string) || '').slice(0, 10)
}

export default function TimelinePage() {
  const { selection } = useSelection()
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!selection.companyId) {
      setRows([])
      return
    }
    const q = new URLSearchParams()
    q.set('company_id', String(selection.companyId))
    if (selection.contactId) q.set('contact_id', String(selection.contactId))
    apiFetch<Record<string, unknown>[]>(`/api/timeline?${q}`)
      .then(setRows)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }, [selection.companyId, selection.contactId])

  if (!selection.companyId) {
    return <div className="empty-state"><p>Select a company.</p></div>
  }

  return (
    <div className="page-wide">
      <h1>Timeline</h1>
      {err && <p className="error">{err}</p>}
      <ul className="timeline">
        {rows.length === 0 && <li className="muted">No entries.</li>}
        {rows.map((a, i) => (
          <li key={i}>
            <span className="tl-date">{timelineDisplayDate(a)}</span>
            <span className="tl-body">
              <span className="tl-status-pill">{String(a.status || '')}</span>{' '}
              {String(a.action_type)} via {String(a.channel)} — {String(a.description || '').slice(0, 200)}
              {(a.outreach_sync_meta as { source?: string } | undefined)?.source === 'gmail_sync' ? (
                <span className="outreach-sync-badge">Gmail</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
