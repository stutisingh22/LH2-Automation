import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { useSearchParams } from 'react-router-dom'

export default function PipelinePage() {
  const { selection } = useSelection()
  const meta = useMeta()
  const [searchParams] = useSearchParams()
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [err, setErr] = useState('')

  const src: 'independent' | 'media' = searchParams.get('source') === 'media' ? 'media' : selection.source

  useEffect(() => {
    setErr('')
    setData(null)
    const path = src === 'media' ? '/api/pipeline/media-houses' : '/api/pipeline/independent'
    apiFetch<Record<string, unknown>>(path)
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }, [src])

  async function patchMediaAdvisor(childId: number, status: string) {
    await apiFetch(`/api/pipeline/media-houses/companies/${childId}`, {
      method: 'PATCH',
      json: { status },
    })
    const path = '/api/pipeline/media-houses'
    apiFetch<Record<string, unknown>>(path, { silent: true }).then(setData)
  }

  const labels = meta?.contact_status_labels || {}

  if (!data) {
    return err ? <p className="error">{err}</p> : <p className="muted">Loading…</p>
  }

  if (src === 'independent') {
    const pipeline =
      (data.pipeline as Record<string, { contact: Record<string, unknown>; company: Record<string, unknown> }[]>) || {}
    const cols = (data.columns as string[]) || []
    const kpi = (data.kpi as Record<string, number>) || {}
    return (
      <div className="page-wide pipeline-page">
        <h1>Pipeline</h1>
        {cols.length === 0 && <p className="muted">No pipeline stages available yet.</p>}
        <div className="pipe-kpi">
          {cols.map((s) => (
            <span key={s} className="kpi sm">
              {kpi[s] ?? 0} {labels[s] || s}
            </span>
          ))}
        </div>
        <div className="pipe-cols">
          {cols.map((s) => (
            <div key={s} className="pipe-col">
              <h3>
                {labels[s] || s}
                <span className="pipe-col-count">{(pipeline[s] || []).length}</span>
              </h3>
              <ul>
                {(pipeline[s] || []).length === 0 ? (
                  <li className="muted pipe-empty">No contacts</li>
                ) : (
                  (pipeline[s] || []).map((item) => (
                    <li key={item.contact.id as number}>
                      <strong>{String(item.contact.name)}</strong>
                      <span className="pipe-domain">{String(item.company.domain)}</span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const groups = (data.groups as { parent_name: string; children: { child: Record<string, unknown>; spoc_name: string; status: string }[] }[]) || []
  return (
    <div className="page-wide pipeline-page">
      <h1>Media houses pipeline</h1>
      {groups.length === 0 && <p className="muted">No media house groups available yet.</p>}
      {groups.map((g) => (
        <section key={g.parent_name} className="panel pipeline-parent-card">
          <h2>
            {g.parent_name}
            <span className="pipe-col-count">{g.children.length}</span>
          </h2>
          <ul>
            {g.children.map((it) => {
              const ch = it.child as { id: number; domain: string }
              return (
                <li key={ch.id} className="pipe-media-row">
                  <span className="pipe-domain">{ch.domain}</span>
                  <select
                    value={it.status}
                    title={`Update status for ${ch.domain}`}
                    onChange={(e) => patchMediaAdvisor(ch.id, e.target.value)}
                  >
                    {(meta?.contact_statuses || []).map((s) => (
                      <option key={s} value={s}>
                        {labels[s] || s}
                      </option>
                    ))}
                  </select>
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </div>
  )
}
