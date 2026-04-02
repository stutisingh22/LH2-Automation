import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { toWorkspaceSearchString } from '../lib/workspaceUrl'
import { useSelection } from '../state/SelectionContext'

export default function HistoryPage() {
  const nav = useNavigate()
  const { setSelection } = useSelection()
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [err, setErr] = useState('')

  function reload() {
    apiFetch<Record<string, unknown>>('/api/history/summary')
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }

  useEffect(() => {
    reload()
  }, [])

  async function delSession(id: number) {
    await apiFetch(`/api/history/sessions/${id}`, { method: 'DELETE' })
    reload()
  }

  function openSession(sx: Record<string, unknown>) {
    let aid: number | null =
      typeof sx.source_advisor_company_id === 'number'
        ? sx.source_advisor_company_id
        : sx.source_advisor_company_id != null
          ? Number(sx.source_advisor_company_id)
          : null
    if (!Number.isFinite(aid as number)) aid = null
    if (aid == null && typeof sx.company_domain === 'string') {
      const m = /^__advisor_(\d+)$/.exec(sx.company_domain.trim())
      if (m) aid = Number(m[1])
    }
    const nextSel = {
      source: (aid ? 'media' : 'independent') as 'media' | 'independent',
      companyId: sx.company_id as number,
      contactId: (sx.contact_id as number) || null,
      sessionId: sx.id as number,
      advisorCompanyId: aid,
    }
    setSelection(nextSel)
    nav(`/intelligence?${toWorkspaceSearchString(nextSel)}`)
  }

  if (!data) return err ? <p className="error">{err}</p> : <p className="muted">Loading…</p>

  const sessions = (data.sessions as Record<string, unknown>[]) || []
  const analytics = data.analytics as Record<string, unknown> | null

  function sessionCompanyLine(sx: Record<string, unknown>): string {
    const w = sx.company_workspace_label
    if (typeof w === 'string' && w.trim()) return w
    return String(sx.company_domain || sx.company_name || '—')
  }

  function sessionContactLine(sx: Record<string, unknown>): string {
    const n = sx.contact_name
    if (n == null || n === '') return '—'
    return String(n)
  }

  return (
    <div className="page-wide">
      <h1>History</h1>
      {analytics && (
        <section className="panel">
          <h2>Analytics</h2>
          <p className="muted">
            Total {String(analytics.total)} · Sent {String(analytics.sent)} · Positive rate{' '}
            {String(analytics.positive_rate)}
          </p>
        </section>
      )}
      <section className="panel">
        <h2>Sessions</h2>
        <ul className="session-list">
          {sessions.map((sx) => (
            <li key={sx.id as number} className="session-row">
              <div>
                <strong>{sessionCompanyLine(sx)}</strong> · {sessionContactLine(sx)}
                <span className="muted small">
                  {' '}
                  ({String(sx.message_count)} msgs · {String(sx.action_count)} actions)
                </span>
              </div>
              <div className="btn-row tight">
                <button type="button" className="secondary sm" onClick={() => openSession(sx)}>
                  Open
                </button>
                <button type="button" className="ghost sm" onClick={() => delSession(sx.id as number)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
