import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { apiFetch, downloadAuthorized, type User } from '../api/client'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'

type Step = Record<string, unknown>
type Seq = { id: string; steps: Step[]; done: number; total: number }

type SessionDetail = {
  session: Record<string, unknown>
  contact?: { id?: number; email?: string | null; name?: string | null }
  company?: { domain?: string; name?: string }
}

function flattenSteps(board: { sequences: Seq[]; standalone: Step[] } | null): Step[] {
  if (!board) return []
  const out: Step[] = [...board.standalone]
  for (const seq of board.sequences) {
    out.push(...seq.steps)
  }
  return out
}

function stepDraftText(st: Step): string {
  const raw = String(st.content_sent || '').trim()
  if (raw) return normalizeEmailLikeText(raw)
  const revs = Array.isArray(st.draft_revisions) ? (st.draft_revisions as unknown[]) : []
  if (revs.length > 0) return normalizeEmailLikeText(String(revs[revs.length - 1] || '').trim())
  return ''
}

function stepReplyText(st: Step): string {
  return normalizeEmailLikeText(String(st.response_content || '').trim())
}

function stepOutcomeText(st: Step): string {
  return String(st.outcome || '').trim()
}

function hasGmailSyncMeta(st: Step): boolean {
  const m = st.outreach_sync_meta as Record<string, unknown> | null | undefined
  return m != null && typeof m === 'object' && String(m.source || '') === 'gmail_sync'
}

function normalizeEmailLikeText(input: string): string {
  let t = (input || '').replace(/\r/g, '').trim()
  if (!t) return ''

  // If legacy full-thread blob is present, show only first outbound message block.
  const firstMarker = t.match(/--- Message 1 \|[^\n]*---\n?/i)
  if (firstMarker && firstMarker.index != null) {
    const start = firstMarker.index + firstMarker[0].length
    const rest = t.slice(start)
    const nextIdx = rest.search(/\n--- Message \d+ \|/i)
    t = (nextIdx >= 0 ? rest.slice(0, nextIdx) : rest).trim()
  }

  // Remove quoted history sections.
  const cutPatterns = [
    /(^|\n)\s*>?\s*On .+ wrote:\s*$/im,
    /(^|\n)\s*>?\s*On .+\n\s*>?\s*wrote:\s*$/im,
    /\nFrom:\s.+\nSent:\s.+\nTo:\s.+\nSubject:\s.+/im,
    /\nFrom:\s.+\nDate:\s.+\nSubject:\s.+/im,
    /\n-{2,}\s*Original Message\s*-{2,}/im,
    /\nBegin forwarded message:/im,
    /\nForwarded message:/im,
  ]
  for (const p of cutPatterns) {
    const idx = t.search(p)
    if (idx >= 0) {
      t = t.slice(0, idx).trim()
      break
    }
  }

  // Strip quote prefixes from copied email text.
  t = t
    .split('\n')
    .map((line) => line.replace(/^\s*>+\s?/, ''))
    .filter((line, i, arr) => {
      // Drop noisy empty runs; keep intentional paragraph gaps.
      if (line.trim()) return true
      const prev = i > 0 ? arr[i - 1].trim() : ''
      const next = i < arr.length - 1 ? arr[i + 1].trim() : ''
      return !!prev && !!next
    })
    .join('\n')
    .trim()

  return t
}

function dateSliceFromScheduled(st: Step): string {
  const r = st.scheduled_at
  if (r == null || r === undefined) return ''
  return String(r).slice(0, 10)
}

/** Calendar date → ISO instant (noon UTC) for stable planned-day storage */
function ymdToScheduledIso(ymd: string): string {
  const [y, m, d] = ymd.split('-').map((x) => parseInt(x, 10))
  if (!y || !m || !d) return ''
  return new Date(Date.UTC(y, m - 1, d, 12, 0, 0)).toISOString()
}

export default function ActionsPage() {
  const { selection } = useSelection()
  const meta = useMeta()
  const sid = selection.sessionId
  const [board, setBoard] = useState<{ sequences: Seq[]; standalone: Step[] } | null>(null)
  const [err, setErr] = useState('')
  const [sel, setSel] = useState<number[]>([])
  const [sessionDetail, setSessionDetail] = useState<SessionDetail | null>(null)
  const [me, setMe] = useState<User | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [bodyDraft, setBodyDraft] = useState('')
  const [refineInstr, setRefineInstr] = useState('')
  const [refineBusy, setRefineBusy] = useState(false)
  const [adaptFocus, setAdaptFocus] = useState('')
  const [adaptBusy, setAdaptBusy] = useState(false)
  const [replanResp, setReplanResp] = useState('')
  const [replanTrigger, setReplanTrigger] = useState<number | ''>('')
  const [replanOut, setReplanOut] = useState('')
  const [replanBusy, setReplanBusy] = useState(false)
  const [productKey, setProductKey] = useState('accelerate')
  const [gmailConnected, setGmailConnected] = useState<boolean | null>(null)
  const [uiMsg, setUiMsg] = useState('')
  const [activeRevisionIndex, setActiveRevisionIndex] = useState<number | null>(null)
  const [logDesc, setLogDesc] = useState('')
  const [logChannel, setLogChannel] = useState('Email')
  const [logContent, setLogContent] = useState('')

  const products = meta?.products?.length ? meta.products : [{ key: 'accelerate', label: 'Accelerate' }]

  const refreshBoard = () => {
    if (!sid) return
    apiFetch<{ sequences: Seq[]; standalone: Step[] }>(`/api/sessions/${sid}/action-board`)
      .then(setBoard)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Failed'))
  }

  useEffect(() => {
    if (!sid) {
      setBoard(null)
      setSessionDetail(null)
      return
    }
    refreshBoard()
    apiFetch<SessionDetail>(`/api/sessions/${sid}`)
      .then(setSessionDetail)
      .catch(() => setSessionDetail(null))
  }, [sid])

  useEffect(() => {
    apiFetch<User>('/api/auth/me', { silent: true })
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  const accountEmail = (me?.email || '').trim()

  useEffect(() => {
    if (!accountEmail) {
      setGmailConnected(null)
      return
    }
    apiFetch<{ connected: boolean }>(`/api/gmail/status?account_email=${encodeURIComponent(accountEmail)}`, {
      silent: true,
    })
      .then((r) => setGmailConnected(r.connected))
      .catch(() => setGmailConnected(null))
  }, [accountEmail])

  const allSteps = useMemo(() => flattenSteps(board), [board])
  const detailStep = useMemo(() => {
    if (detailId == null) return null
    return allSteps.find((s) => s.id === detailId) || null
  }, [allSteps, detailId])

  useEffect(() => {
    if (!detailStep) {
      setBodyDraft('')
      return
    }
    setBodyDraft(String(detailStep.content_sent || ''))
    setActiveRevisionIndex(null)
  }, [detailStep])

  async function patchStatus(id: number, status: string) {
    await apiFetch(`/api/actions/${id}`, { method: 'PATCH', json: { status } })
    refreshBoard()
  }

  async function patchPlannedDate(id: number, ymd: string, shiftFollowingInSequence: boolean) {
    if (!ymd) return
    setErr('')
    try {
      await apiFetch(`/api/actions/${id}`, {
        method: 'PATCH',
        silent: true,
        json: {
          scheduled_at: ymdToScheduledIso(ymd),
          shift_following_in_sequence: shiftFollowingInSequence,
        },
      })
      setUiMsg(shiftFollowingInSequence ? 'Date updated; later steps shifted by the same amount.' : 'Date updated.')
      refreshBoard()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not update date')
    }
  }

  async function bulkDel() {
    if (!sid || !sel.length) return
    await apiFetch(`/api/sessions/${sid}/actions/bulk-delete`, {
      method: 'POST',
      json: { action_ids: sel },
    })
    setSel([])
    refreshBoard()
  }

  async function saveBody() {
    if (detailId == null) return
    await apiFetch(`/api/actions/${detailId}`, {
      method: 'PATCH',
      json: { content_sent: bodyDraft },
    })
    setUiMsg('Draft body saved.')
    refreshBoard()
  }

  async function runRefine() {
    if (detailId == null || !refineInstr.trim()) return
    setRefineBusy(true)
    setErr('')
    try {
      await apiFetch(`/api/actions/${detailId}/refine-draft`, {
        method: 'POST',
        silent: true,
        json: { instruction: refineInstr.trim(), draft_type: 'email' },
      })
      setRefineInstr('')
      refreshBoard()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Refine failed')
    } finally {
      setRefineBusy(false)
    }
  }

  async function runAdaptFollowing() {
    if (detailId == null || !detailStep) return
    const sq = detailStep.sequence_id != null && String(detailStep.sequence_id || '').trim()
    if (!sq) {
      setErr('Open a step that belongs to a sequence.')
      return
    }
    if (!bodyDraft.trim()) {
      setErr('Add draft text for this step first.')
      return
    }
    setAdaptBusy(true)
    setErr('')
    try {
      const r = await apiFetch<{ count: number }>(`/api/actions/${detailId}/adapt-following-drafts`, {
        method: 'POST',
        silent: true,
        json: {
          instruction: adaptFocus.trim(),
          anchor_body: bodyDraft.trim(),
        },
      })
      setUiMsg(
        r.count > 0
          ? `Aligned ${r.count} later step(s). Review those drafts next.`
          : 'No later steps updated — add draft text on following steps, or try an optional focus hint.',
      )
      refreshBoard()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Align later steps failed')
    } finally {
      setAdaptBusy(false)
    }
  }

  async function createGmailDraft() {
    if (!detailId || !sid) return
    const to = String(sessionDetail?.contact?.email || '').trim()
    if (!accountEmail) {
      setErr('Your user account needs an email for Gmail token lookup.')
      return
    }
    setErr('')
    try {
      await apiFetch(`/api/gmail/draft`, {
        method: 'POST',
        json: {
          account_email: accountEmail,
          to_email: to,
          content: bodyDraft,
          product_key: productKey,
          step_index: detailStep?.sequence_step != null ? Number(detailStep.sequence_step) : null,
        },
      })
      setUiMsg(to ? `Gmail draft created for ${to}.` : 'Gmail draft created without a recipient.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Gmail failed')
    }
  }

  async function createGmailDraftForStep(st: Step) {
    const content = stepDraftText(st)
    if (!content) {
      setErr('No draft content to save for this action.')
      return
    }
    if (!accountEmail) {
      setErr('Your user account needs an email for Gmail token lookup.')
      return
    }
    const to = String(sessionDetail?.contact?.email || '').trim()
    setErr('')
    try {
      await apiFetch(`/api/gmail/draft`, {
        method: 'POST',
        json: {
          account_email: accountEmail,
          to_email: to,
          content,
          product_key: productKey,
          step_index: st.sequence_step != null ? Number(st.sequence_step) : null,
        },
      })
      setUiMsg(to ? `Gmail draft created for ${to}.` : 'Gmail draft created without a recipient.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Gmail failed')
    }
  }

  async function runReplan(e: FormEvent) {
    e.preventDefault()
    if (!sid || replanTrigger === '' || !replanResp.trim()) return
    setReplanBusy(true)
    setReplanOut('')
    setErr('')
    try {
      const r = await apiFetch<{ advice: string }>(`/api/sessions/${sid}/replan`, {
        method: 'POST',
        silent: true,
        json: {
          trigger_action_id: Number(replanTrigger),
          response_text: replanResp.trim(),
          product_key: productKey,
        },
      })
      setReplanOut(r.advice || '')
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Replan failed')
    } finally {
      setReplanBusy(false)
    }
  }

  async function logNewAction(e: FormEvent) {
    e.preventDefault()
    if (!sid || !logDesc.trim()) return
    setErr('')
    try {
      await apiFetch(`/api/sessions/${sid}/actions`, {
        method: 'POST',
        json: {
          action_type: 'Outreach',
          channel: logChannel,
          description: logDesc.trim(),
          status: 'planned',
          content_sent: logContent.trim() || null,
        },
      })
      setLogDesc('')
      setLogContent('')
      refreshBoard()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Log failed')
    }
  }

  const statuses = meta?.action_statuses || []
  const seqOnly = useMemo(() => {
    if (!board) return [] as Step[]
    const out: Step[] = []
    for (const seq of board.sequences) {
      out.push(...seq.steps)
    }
    return out
  }, [board])

  const revisions = detailStep && Array.isArray(detailStep.draft_revisions)
    ? (detailStep.draft_revisions as string[])
    : []

  if (!selection.companyId) {
    return (
      <div className="empty-state">
        <p>Select a company and contact first.</p>
      </div>
    )
  }
  if (!sid) {
    return (
      <div className="empty-state">
        <p>Open Strategist and pick a contact to load actions.</p>
      </div>
    )
  }

  return (
    <div className="page-wide actions-page">
      <header className="intel-hero strategist-header">
        <div className="intel-hero-text">
          <p className="intel-eyebrow">Actions</p>
          <h1>{String(sessionDetail?.company?.name || sessionDetail?.company?.domain || 'Actions')}</h1>
          <p className="intel-domain muted">{String(sessionDetail?.company?.domain || '')}</p>
        </div>
      </header>
      {err && <p className="error">{err}</p>}
      {uiMsg && <p className="ok">{uiMsg}</p>}

      <div className="toolbar">
        <button
          type="button"
          className="secondary sm"
          onClick={() =>
            downloadAuthorized(`/api/sessions/${sid}/export.csv`, `export_${sid}.csv`)
          }
        >
          Export CSV
        </button>
        {sel.length > 0 && (
          <button type="button" className="secondary sm" onClick={bulkDel}>
            Delete selected ({sel.length})
          </button>
        )}
        {accountEmail && (
          <span className="muted small">
            Gmail token: {gmailConnected === null ? '…' : gmailConnected ? 'connected' : 'missing'}
          </span>
        )}
      </div>

      <div className="actions-top-grid">
        <details className="panel actions-collapsible">
          <summary>Log action</summary>
          <form className="stack-form" onSubmit={logNewAction}>
            <label>
              Description
              <input value={logDesc} onChange={(e) => setLogDesc(e.target.value)} placeholder="Step summary" />
            </label>
            <label>
              Channel
              <input value={logChannel} onChange={(e) => setLogChannel(e.target.value)} />
            </label>
            <label>
              Draft / notes (optional)
              <textarea rows={4} value={logContent} onChange={(e) => setLogContent(e.target.value)} />
            </label>
            <button type="submit" className="primary sm">
              Log
            </button>
          </form>
        </details>

        <details className="panel actions-collapsible">
          <summary>Replan remaining steps</summary>
          <p className="muted small">After a reply, get AI suggestions for the rest of the sequence.</p>
          <form className="stack-form" onSubmit={runReplan}>
            <label>
              Trigger step (action id)
              <select
                aria-label="Replan trigger action"
                value={replanTrigger === '' ? '' : String(replanTrigger)}
                onChange={(e) => setReplanTrigger(e.target.value ? Number(e.target.value) : '')}
              >
                <option value="">Select…</option>
                {seqOnly.map((st) => (
                  <option key={String(st.id)} value={String(st.id)}>
                    #{String(st.sequence_step)} · {String(st.description || '').slice(0, 60)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Response text
              <textarea rows={4} value={replanResp} onChange={(e) => setReplanResp(e.target.value)} placeholder="What they said…" />
            </label>
            <div className="product-row compact">
              {products.map((p) => (
                <label key={p.key} className="radio-pill">
                  <input
                    type="radio"
                    name="replan-pk"
                    checked={productKey === p.key}
                    onChange={() => setProductKey(p.key)}
                  />
                  {p.label}
                </label>
              ))}
            </div>
            <button type="submit" className="primary sm" disabled={replanBusy}>
              {replanBusy ? '…' : 'Run replan'}
            </button>
          </form>
          {replanOut && <div className="replan-out muted small">{replanOut}</div>}
        </details>
      </div>

      <div className="actions-content-grid">
        <div className="actions-list-col">
          {!board ? (
            <p className="muted">Loading…</p>
          ) : (
            <>
              {board.sequences.map((seq) => (
                <section key={seq.id} className="panel action-seq-panel">
                  <h2>
                    Sequence {seq.id} ({seq.done}/{seq.total})
                  </h2>
                  <ul className="action-steps">
                    {seq.steps.map((st) => {
                      const id = st.id as number
                      return (
                        <li key={id} className="step-row">
                          <label className="chk">
                            <input
                              type="checkbox"
                              aria-label={`Select action ${id}`}
                              checked={sel.includes(id)}
                              onChange={(e) =>
                                setSel((s) => (e.target.checked ? [...s, id] : s.filter((x) => x !== id)))
                              }
                            />
                          </label>
                          <details className="action-step-details">
                            <summary className="step-summary">
                              <span className="step-main">
                                <strong>
                                  #{String(st.sequence_step)} {String(st.channel)} — {String(st.status)}
                                </strong>
                                {hasGmailSyncMeta(st) ? (
                                  <span className="outreach-sync-badge" title="Status/outcome updated from Gmail sync">
                                    Gmail sync
                                  </span>
                                ) : null}
                                {dateSliceFromScheduled(st) ? (
                                  <span className="muted small"> · {dateSliceFromScheduled(st)}</span>
                                ) : null}
                              </span>
                            </summary>
                            <div className="step-detail-body">
                              <div className="muted small">{String(st.description || '').slice(0, 220)}</div>
                              <label className="step-plan-date">
                                <span className="muted small">Planned date</span>
                                <input
                                  type="date"
                                  aria-label={`Planned date step ${String(st.sequence_step)}`}
                                  value={dateSliceFromScheduled(st)}
                                  onChange={(e) =>
                                    void patchPlannedDate(id, e.target.value, true)
                                  }
                                />
                              </label>
                              <p className="muted small step-plan-date-shift-hint">
                                Changing this moves later steps with dates by the same number of days.
                              </p>
                              {stepDraftText(st) ? (
                                <pre className="action-step-draft-preview">{stepDraftText(st)}</pre>
                              ) : (
                                <p className="muted small">No draft text saved for this step yet.</p>
                              )}
                              {stepReplyText(st) ? (
                                <>
                                  <div className="muted small">Reply captured</div>
                                  <pre className="action-step-draft-preview">{stepReplyText(st)}</pre>
                                </>
                              ) : null}
                              {stepOutcomeText(st) ? (
                                <>
                                  <div className="muted small">Outcome</div>
                                  <pre className="action-step-draft-preview">{stepOutcomeText(st)}</pre>
                                </>
                              ) : null}
                              <div className="step-actions">
                                <label className="sr-only" htmlFor={`act-st-seq-${id}`}>
                                  Status
                                </label>
                                <select
                                  id={`act-st-seq-${id}`}
                                  aria-label="Sequence step status"
                                  value={String(st.status)}
                                  onChange={(e) => patchStatus(id, e.target.value)}
                                >
                                  {statuses.map((s) => (
                                    <option key={s} value={s}>
                                      {s}
                                    </option>
                                  ))}
                                </select>
                                <button type="button" className="secondary sm" onClick={() => setDetailId(id)}>
                                  Edit draft
                                </button>
                                <button
                                  type="button"
                                  className="secondary sm"
                                  onClick={() => void createGmailDraftForStep(st)}
                                  disabled={!accountEmail}
                                >
                                  Gmail draft
                                </button>
                              </div>
                            </div>
                          </details>
                        </li>
                      )
                    })}
                  </ul>
                </section>
              ))}
              {board.standalone.length > 0 && (
                <section className="panel action-seq-panel">
                  <h2>Standalone</h2>
                  <ul className="action-steps">
                    {board.standalone.map((st) => {
                      const id = st.id as number
                      return (
                        <li key={id} className="step-row">
                          <label className="chk">
                            <input
                              type="checkbox"
                              aria-label={`Select action ${id}`}
                              checked={sel.includes(id)}
                              onChange={(e) =>
                                setSel((s) => (e.target.checked ? [...s, id] : s.filter((x) => x !== id)))
                              }
                            />
                          </label>
                          <details className="action-step-details">
                            <summary className="step-summary">
                              <span className="step-main">
                                <strong>
                                  {String(st.action_type)} · {String(st.channel)} — {String(st.status)}
                                </strong>
                                {hasGmailSyncMeta(st) ? (
                                  <span className="outreach-sync-badge" title="Status/outcome updated from Gmail sync">
                                    Gmail sync
                                  </span>
                                ) : null}
                              </span>
                            </summary>
                            <div className="step-detail-body">
                              <div className="muted small">{String(st.description || '').slice(0, 220)}</div>
                              <label className="step-plan-date">
                                <span className="muted small">Planned date</span>
                                <input
                                  type="date"
                                  aria-label="Planned date standalone action"
                                  value={dateSliceFromScheduled(st)}
                                  onChange={(e) => void patchPlannedDate(id, e.target.value, false)}
                                />
                              </label>
                              {stepDraftText(st) ? (
                                <pre className="action-step-draft-preview">{stepDraftText(st)}</pre>
                              ) : (
                                <p className="muted small">No draft text saved for this action yet.</p>
                              )}
                              {stepReplyText(st) ? (
                                <>
                                  <div className="muted small">Reply captured</div>
                                  <pre className="action-step-draft-preview">{stepReplyText(st)}</pre>
                                </>
                              ) : null}
                              {stepOutcomeText(st) ? (
                                <>
                                  <div className="muted small">Outcome</div>
                                  <pre className="action-step-draft-preview">{stepOutcomeText(st)}</pre>
                                </>
                              ) : null}
                              <div className="step-actions">
                                <label className="sr-only" htmlFor={`act-st-sa-${id}`}>
                                  Status
                                </label>
                                <select
                                  id={`act-st-sa-${id}`}
                                  aria-label="Standalone action status"
                                  value={String(st.status)}
                                  onChange={(e) => patchStatus(id, e.target.value)}
                                >
                                  {statuses.map((s) => (
                                    <option key={s} value={s}>
                                      {s}
                                    </option>
                                  ))}
                                </select>
                                <button type="button" className="secondary sm" onClick={() => setDetailId(id)}>
                                  Edit draft
                                </button>
                                <button
                                  type="button"
                                  className="secondary sm"
                                  onClick={() => void createGmailDraftForStep(st)}
                                  disabled={!accountEmail}
                                >
                                  Gmail draft
                                </button>
                              </div>
                            </div>
                          </details>
                        </li>
                      )
                    })}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>

        <aside className="actions-editor-col">
          {detailId != null && detailStep ? (
            <section className="panel action-detail">
              <div className="detail-head">
                <h2>Action #{detailId}</h2>
                <button type="button" className="ghost sm" onClick={() => setDetailId(null)}>
                  Close
                </button>
              </div>
              <label className="step-plan-date">
                <span className="muted small">Planned date</span>
                <input
                  type="date"
                  aria-label="Planned date for selected action"
                  value={dateSliceFromScheduled(detailStep)}
                  onChange={(e) =>
                    void patchPlannedDate(
                      detailId,
                      e.target.value,
                      Boolean(
                        detailStep.sequence_id != null &&
                          String(detailStep.sequence_id || '').trim(),
                      ),
                    )
                  }
                />
              </label>
              {(detailStep.sequence_id != null && String(detailStep.sequence_id || '').trim()) ? (
                <p className="muted small">Later dated steps in this sequence shift together.</p>
              ) : null}
              <label>
                Body / draft
                <textarea rows={14} value={bodyDraft} onChange={(e) => setBodyDraft(e.target.value)} />
              </label>
              <div className="btn-row">
                <button type="button" className="secondary sm" onClick={saveBody}>
                  Save body
                </button>
                <button type="button" className="secondary sm" onClick={createGmailDraft} disabled={!accountEmail}>
                  Gmail draft
                </button>
              </div>
              <p className="muted small">Gmail draft can be created even if recipient email is missing.</p>
              {revisions.length > 0 && (
                <div className="rev-chooser">
                  <label>
                    Version
                    <select
                      value={activeRevisionIndex == null ? 'current' : String(activeRevisionIndex)}
                      onChange={(e) => {
                        const v = e.target.value
                        if (v === 'current') {
                          setActiveRevisionIndex(null)
                          setBodyDraft(String(detailStep.content_sent || ''))
                          return
                        }
                        const idx = Number(v)
                        if (!Number.isFinite(idx)) return
                        setActiveRevisionIndex(idx)
                        setBodyDraft(revisions[idx] || '')
                      }}
                    >
                      <option value="current">Current (draft body)</option>
                      {revisions.map((_, i) => (
                        <option key={i} value={String(i)}>
                          v{i + 1}
                        </option>
                      ))}
                    </select>
                  </label>
                  {activeRevisionIndex != null && (
                    <p className="muted small">Editing v{activeRevisionIndex + 1}</p>
                  )}
                </div>
              )}
              <div className="refine-row">
                <label>
                  Refine instruction
                  <input
                    value={refineInstr}
                    onChange={(e) => setRefineInstr(e.target.value)}
                    placeholder="e.g. Shorten; warmer tone"
                  />
                </label>
                <button type="button" className="primary sm" onClick={runRefine} disabled={refineBusy}>
                  {refineBusy ? '…' : 'Refine'}
                </button>
              </div>
              {detailStep.sequence_id != null && String(detailStep.sequence_id || '').trim() ? (
                <div className="refine-row adapt-following-block">
                  <label>
                    Optional focus for later emails
                    <input
                      value={adaptFocus}
                      onChange={(e) => setAdaptFocus(e.target.value)}
                      placeholder="e.g. Emphasize rev share; keep consultative tone"
                    />
                  </label>
                  <button
                    type="button"
                    className="secondary sm"
                    onClick={() => void runAdaptFollowing()}
                    disabled={adaptBusy || refineBusy || !bodyDraft.trim()}
                  >
                    {adaptBusy ? '…' : 'Align later steps'}
                  </button>
                  <p className="muted small adapt-following-hint">
                    Uses the draft in the editor (saved or not) as the source of truth so tone and new topics
                    carry into the next steps naturally.
                  </p>
                </div>
              ) : null}
            </section>
          ) : (
            <section className="panel action-detail-placeholder">
              <h2>Draft editor</h2>
              <p className="muted">Select any action and click “Edit draft” to open editor and Gmail actions.</p>
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
