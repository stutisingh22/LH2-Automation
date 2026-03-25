import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'

type Summary = {
  stats: Record<string, number>
  companies_count: number
  overdue: Record<string, unknown>[]
  todays: Record<string, unknown>[]
  recent: Record<string, unknown>[]
}

export default function DashboardPage() {
  const { selection } = useSelection()
  const meta = useMeta()
  const [data, setData] = useState<Summary | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const src = selection.source === 'media' ? 'media' : 'independent'
    apiFetch<Summary>(`/api/dashboard/summary?source=${src}`)
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }, [selection.source])

  const strip = meta?.contact_status_labels_strip

  return (
    <div className="page-wide">
      <h1>Dashboard</h1>
      {err && <p className="error">{err}</p>}
      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <section className="panel">
            <h2>Overview</h2>
            <div className="kpi-row">
              <div className="kpi">
                <span className="kpi-n">{data.companies_count}</span>
                <span className="kpi-l">Companies</span>
              </div>
              <div className="kpi">
                <span className="kpi-n">{data.stats.outreach_sent ?? 0}</span>
                <span className="kpi-l">Active outreach</span>
              </div>
              <div className="kpi">
                <span className="kpi-n">{data.stats.meeting_booked ?? 0}</span>
                <span className="kpi-l">Meetings</span>
              </div>
              <div className="kpi">
                <span className="kpi-n">{data.stats.partnership_active ?? 0}</span>
                <span className="kpi-l">Partnerships</span>
              </div>
            </div>
          </section>
          <section className="panel">
            <h2>Pipeline breakdown</h2>
            <div className="status-grid">
              {(meta?.contact_statuses || []).map((s) => (
                <div key={s} className="status-cell">
                  <span className="status-n">{data.stats[s] ?? 0}</span>
                  <span className="status-l">{strip?.[s] || s}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="panel">
            <h2>Alerts</h2>
            <div className="two-col">
              <div>
                <h3 className="subh">Overdue</h3>
                <ul className="alert-list">
                  {data.overdue.length === 0 && <li className="muted">None</li>}
                  {data.overdue.map((a, i) => (
                    <li key={i}>{String((a as { description?: string }).description || '').slice(0, 120)}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="subh">Today</h3>
                <ul className="alert-list">
                  {data.todays.length === 0 && <li className="muted">None</li>}
                  {data.todays.map((a, i) => (
                    <li key={i}>{String((a as { description?: string }).description || '').slice(0, 120)}</li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
          <section className="panel">
            <h2>Recent activity</h2>
            <ul className="activity-list">
              {data.recent.length === 0 && <li className="muted">No activity yet.</li>}
              {data.recent.map((a, i) => (
                <li key={i}>
                  <span className="act-meta">
                    {(a as { action_type?: string }).action_type} · {(a as { status?: string }).status}
                  </span>
                  <div>{String((a as { description?: string }).description || '').slice(0, 200)}</div>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}
