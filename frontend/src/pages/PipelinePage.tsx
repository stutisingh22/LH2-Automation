import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'

export default function PipelinePage() {
  const { selection } = useSelection()
  const meta = useMeta()
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [err, setErr] = useState('')

  const src = selection.source

  useEffect(() => {
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
    apiFetch<Record<string, unknown>>(path).then(setData)
  }

  const labels = meta?.contact_status_labels || {}

  if (!data) {
    return err ? <p className="error">{err}</p> : <p className="muted">Loading…</p>
  }

  if (src === 'independent') {
    const pipeline = data.pipeline as Record<string, { contact: Record<string, unknown>; company: Record<string, unknown> }[]>
    const cols = (data.columns as string[]) || []
    return (
      <div className="page-wide">
        <h1>Pipeline</h1>
        <div className="pipe-kpi">
          {cols.map((s) => (
            <span key={s} className="kpi sm">
              {(data.kpi as Record<string, number>)[s] ?? 0} {labels[s] || s}
            </span>
          ))}
        </div>
        <div className="pipe-cols">
          {cols.map((s) => (
            <div key={s} className="pipe-col">
              <h3>{labels[s] || s}</h3>
              <ul>
                {(pipeline[s] || []).map((item) => (
                  <li key={item.contact.id as number}>
                    {String(item.contact.name)} · {String(item.company.domain)}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const groups = (data.groups as { parent_name: string; children: { child: Record<string, unknown>; spoc_name: string; status: string }[] }[]) || []
  return (
    <div className="page-wide">
      <h1>Media houses pipeline</h1>
      {groups.map((g) => (
        <section key={g.parent_name} className="panel">
          <h2>{g.parent_name}</h2>
          <ul>
            {g.children.map((it) => {
              const ch = it.child as { id: number; domain: string }
              return (
                <li key={ch.id} className="pipe-media-row">
                  <span>{ch.domain}</span>
                  <select
                    value={it.status}
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
