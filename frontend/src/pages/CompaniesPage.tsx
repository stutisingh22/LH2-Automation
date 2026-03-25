import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch, type Company } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useDebounced } from '../hooks/useDebounced'

type AdvisorGroup = { parent_name: string; children: { id: number; domain: string; parent_name: string }[] }

export default function CompaniesPage() {
  const { selection, setSelection } = useSelection()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const dq = useDebounced(q, 280)
  const [rows, setRows] = useState<Company[] | unknown[]>([])
  const [groups, setGroups] = useState<AdvisorGroup[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  const src = selection.source

  useEffect(() => {
    let c = true
    setLoading(true)
    setErr('')
    ;(async () => {
      try {
        if (src === 'media') {
          if (dq.trim()) {
            const flat = await apiFetch<unknown[]>(`/api/companies?source=media&q=${encodeURIComponent(dq)}`)
            if (!c) return
            const byParent: Record<string, AdvisorGroup['children']> = {}
            for (const row of flat as { parent_name?: string; id?: number; domain?: string }[]) {
              const p = row.parent_name || '(No parent)'
              if (!byParent[p]) byParent[p] = []
              byParent[p].push(row as AdvisorGroup['children'][0])
            }
            setGroups(
              Object.entries(byParent).map(([parent_name, children]) => ({ parent_name, children })),
            )
            setRows([])
          } else {
            const g = await apiFetch<AdvisorGroup[]>('/api/advisor/media-houses')
            if (!c) return
            setGroups(g)
            setRows([])
          }
        } else {
          const data = await apiFetch<Company[]>(`/api/companies?q=${encodeURIComponent(dq)}`)
          if (!c) return
          setRows(data)
          setGroups(null)
        }
      } catch (e) {
        if (!c) return
        setErr(e instanceof Error ? e.message : 'Load failed')
      } finally {
        if (c) setLoading(false)
      }
    })()
    return () => {
      c = false
    }
  }, [dq, src])

  async function syncIndependent() {
    setBusy('sync-ind')
    try {
      await apiFetch('/api/sync/notion/independent', { method: 'POST' })
    } finally {
      setBusy('')
    }
  }

  async function syncMedia() {
    setBusy('sync-media')
    try {
      await apiFetch('/api/sync/notion/media-houses', { method: 'POST' })
    } finally {
      setBusy('')
    }
  }

  async function pickIndependent(c: Company) {
    setSelection({
      source: 'independent',
      companyId: c.id,
      contactId: null,
      advisorCompanyId: null,
      sessionId: null,
    })
    nav('/intelligence')
  }

  async function pickMediaChild(childId: number) {
    setBusy(`open-${childId}`)
    try {
      const r = await apiFetch<{
        session_id: number
        company_id: number
        contact_id: number
        advisor_company_id: number
      }>(`/api/advisor/companies/${childId}/session`, { method: 'POST' })
      setSelection({
        source: 'media',
        companyId: r.company_id,
        contactId: r.contact_id,
        sessionId: r.session_id,
        advisorCompanyId: r.advisor_company_id,
      })
      nav('/intelligence')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="page-wide">
      <h1>Companies</h1>
      <div className="toolbar">
        <input
          className="search"
          placeholder={src === 'media' ? 'Search domain or parent…' : 'Search domain or name…'}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        {src === 'independent' ? (
          <button type="button" className="secondary" disabled={!!busy} onClick={syncIndependent}>
            {busy === 'sync-ind' ? '…' : 'Sync Notion'}
          </button>
        ) : (
          <button type="button" className="secondary" disabled={!!busy} onClick={syncMedia}>
            {busy === 'sync-media' ? '…' : 'Sync Media Houses'}
          </button>
        )}
      </div>
      {err && <p className="error">{err}</p>}
      {loading ? (
        <p className="muted">Loading…</p>
      ) : src === 'independent' ? (
        <>
          <p className="muted small companies-row-hint">Click a row to open Intelligence for that company.</p>
          <div className="lh2-table-wrap media-houses-table-wrap">
            <table className="lh2-data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Domain</th>
                </tr>
              </thead>
              <tbody>
                {(rows as Company[]).map((c) => (
                  <tr
                    key={c.id}
                    className="lh2-row-intel"
                    tabIndex={0}
                    role="button"
                    onClick={() => pickIndependent(c)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        pickIndependent(c)
                      }
                    }}
                    aria-label={`Open ${c.name || c.domain} in Intelligence`}
                  >
                    <td>
                      <strong>{c.name}</strong>
                    </td>
                    <td className="muted">{c.domain}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <p className="muted small companies-row-hint">Click a row to open Intelligence (media property).</p>
          <div className="lh2-table-wrap media-houses-table-wrap">
            <table className="lh2-data-table">
              <thead>
                <tr>
                  <th>Media house (parent)</th>
                  <th>Domain / property</th>
                </tr>
              </thead>
              <tbody>
                {(groups || []).flatMap((g) =>
                  g.children.map((ch) => (
                    <tr
                      key={ch.id}
                      className="lh2-row-intel"
                      tabIndex={0}
                      role="button"
                      onClick={() => {
                        if (!busy) pickMediaChild(ch.id)
                      }}
                      onKeyDown={(e) => {
                        if (busy) return
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          pickMediaChild(ch.id)
                        }
                      }}
                      aria-label={`Open ${ch.domain} in Intelligence`}
                      aria-busy={busy === `open-${ch.id}`}
                    >
                      <td className="lh2-dt-readonly" style={{ whiteSpace: 'normal' }}>
                        {g.parent_name}
                      </td>
                      <td>
                        <strong>{ch.domain}</strong>
                        {busy === `open-${ch.id}` ? <span className="muted small"> …</span> : null}
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
