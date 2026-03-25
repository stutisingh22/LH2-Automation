import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'

export default function CalendarPage() {
  const { selection } = useSelection()
  const [items, setItems] = useState<{ at: string; action: Record<string, unknown> }[]>([])
  const [err, setErr] = useState('')

  useEffect(() => {
    const src = selection.source === 'media' ? 'media' : 'independent'
    apiFetch<{ items: { at: string; action: Record<string, unknown> }[] }>(
      `/api/calendar/upcoming?source=${src}`,
    )
      .then((r) => setItems(r.items || []))
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }, [selection.source])

  return (
    <div className="page-wide">
      <h1>Calendar</h1>
      {err && <p className="error">{err}</p>}
      <ul className="cal-list">
        {items.length === 0 && <li className="muted">No scheduled actions in the next 90 days.</li>}
        {items.map((it, i) => (
          <li key={i}>
            <strong>{it.at.slice(0, 10)}</strong>{' '}
            {String(it.action.description || '').slice(0, 120)}
          </li>
        ))}
      </ul>
    </div>
  )
}
