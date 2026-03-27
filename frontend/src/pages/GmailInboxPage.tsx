import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiFetch, type User } from '../api/client'

type ThreadRow = {
  id: number
  gmail_thread_id: string
  subject: string | null
  snippet: string | null
  last_message_at: string | null
  gmail_account_id: number
  connected_email: string | null
}

type MsgRow = {
  id: number
  from_email: string | null
  to_emails: string[]
  subject: string | null
  body_snippet: string | null
  body_full?: string | null
  timestamp: string | null
  is_sent: boolean
  thread_label?: string | null
}

function fmtTime(iso: string | null | undefined) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso.slice(0, 16)
  }
}

function renderEmailBody(raw: string): string {
  let t = (raw || '').replace(/\r/g, '').replace(/\u00a0|\u202f/g, ' ').trim()
  if (!t) return ''

  // Gmail quote separators / reply chains.
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
    const m = t.search(p)
    if (m >= 0) {
      t = t.slice(0, m).trim()
      break
    }
  }

  // Remove quoted-prefix blocks.
  const lines = t.split('\n')
  const clean: string[] = []
  for (const ln of lines) {
    const line = ln.replace(/^\s*>+\s?/, '')
    const lt = line.trim()
    // Reply-history markers can be single-line or wrapped to next line.
    if (/^On .+ wrote:\s*$/i.test(lt)) break
    if (/^On .+$/i.test(lt) && clean.length > 0) {
      const nextRaw = lines[clean.length] || ''
      const next = nextRaw.replace(/^\s*>+\s?/, '').trim()
      if (/^wrote:\s*$/i.test(next)) break
    }
    if (/^(From|Sent|To|Cc|Date|Subject):\s+/i.test(lt) && clean.length > 0) break
    if (!line.trim() && clean.length && !clean[clean.length - 1].trim()) continue
    clean.push(line)
  }
  t = clean.join('\n').trim()
  return t
}

function trimAgainstPriorHistory(body: string, priorBodies: string[]): string {
  let out = (body || '').trim()
  if (!out) return out
  for (const prev of priorBodies) {
    const p = (prev || '').trim()
    if (p.length < 40) continue
    const idx = out.indexOf(p)
    if (idx > 0) out = out.slice(0, idx).trim()
  }
  return out
}

function renderThreadPreview(raw: string): string {
  const body = renderEmailBody(raw || '')
  if (!body) return ''
  const one = body.replace(/\s+/g, ' ').trim()
  return one.length > 140 ? `${one.slice(0, 137)}...` : one
}

export default function GmailInboxPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [me, setMe] = useState<User | null>(null)
  const [filterUserId, setFilterUserId] = useState<number | null>(null)
  const [userList, setUserList] = useState<User[]>([])
  const [threads, setThreads] = useState<ThreadRow[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<{ thread: ThreadRow; messages: MsgRow[] } | null>(null)
  const [connections, setConnections] = useState<{ id: number; email: string }[]>([])
  const [onlyKnownContacts, setOnlyKnownContacts] = useState(true)
  const [err, setErr] = useState('')
  const [syncMsg, setSyncMsg] = useState('')
  const [syncBusy, setSyncBusy] = useState(false)

  const isAdmin = (me?.role || '').trim() === 'admin'
  const effectiveUser = filterUserId ?? me?.id ?? null
  const displayMessages = useMemo(() => {
    const msgs = detail?.messages || []
    const out: Array<MsgRow & { display_body: string }> = []
    const history: string[] = []
    for (const m of msgs) {
      const raw = (m.body_full || m.body_snippet || '').trim()
      const cleaned = renderEmailBody(raw)
      const deduped = trimAgainstPriorHistory(cleaned, history)
      const display_body = deduped || cleaned || '(no text)'
      out.push({ ...m, display_body })
      if (display_body && display_body !== '(no text)') history.push(display_body)
    }
    return out
  }, [detail])

  const loadThreads = useCallback(async () => {
    if (!effectiveUser) return
    const params = new URLSearchParams()
    if (isAdmin && filterUserId) params.set('user_id', String(filterUserId))
    params.set('only_known_contacts', onlyKnownContacts ? 'true' : 'false')
    const q = params.toString() ? `?${params.toString()}` : ''
    const res = await apiFetch<{ threads: ThreadRow[] }>(`/api/gmail/threads${q}`)
    setThreads(res.threads || [])
  }, [effectiveUser, filterUserId, isAdmin, onlyKnownContacts])

  const loadConnections = useCallback(async () => {
    if (!effectiveUser) return
    const q = isAdmin && filterUserId ? `?user_id=${filterUserId}` : ''
    const res = await apiFetch<{ accounts: { id: number; email: string }[] }>(`/api/gmail/connections${q}`)
    setConnections((res.accounts || []).map((a) => ({ id: a.id, email: a.email })))
  }, [effectiveUser, filterUserId, isAdmin])

  useEffect(() => {
    apiFetch<User>('/api/auth/me', { silent: true })
      .then(setMe)
      .catch(() => setMe(null))
  }, [])

  useEffect(() => {
    if (!isAdmin) return
    apiFetch<User[]>('/api/users', { silent: true })
      .then(setUserList)
      .catch(() => setUserList([]))
  }, [isAdmin])

  useEffect(() => {
    const c = searchParams.get('connected')
    const e = searchParams.get('error')
    if (c) {
      setSyncMsg('Gmail connected. Run sync to pull recent messages.')
      setSearchParams({}, { replace: true })
    }
    if (e) {
      setErr(`OAuth: ${e}`)
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams])

  useEffect(() => {
    if (!me?.id) return
    if (!isAdmin) setFilterUserId(null)
    loadThreads().catch((e) => setErr(e instanceof Error ? e.message : 'Load failed'))
    loadConnections().catch(() => {})
  }, [me?.id, isAdmin, filterUserId, loadThreads, loadConnections])

  useEffect(() => {
    if (!selectedId || !effectiveUser) {
      setDetail(null)
      return
    }
    const params = new URLSearchParams()
    if (isAdmin && filterUserId) params.set('user_id', String(filterUserId))
    params.set('only_known_contacts', onlyKnownContacts ? 'true' : 'false')
    const q = params.toString() ? `?${params.toString()}` : ''
    apiFetch<{ thread: ThreadRow; messages: MsgRow[] }>(`/api/gmail/threads/${selectedId}${q}`)
      .then(setDetail)
      .catch((e) => setErr(e instanceof Error ? e.message : 'Thread failed'))
  }, [selectedId, effectiveUser, isAdmin, filterUserId, onlyKnownContacts])

  async function connectGmail() {
    setErr('')
    try {
      const { url } = await apiFetch<{ url: string }>('/api/auth/google/login')
      window.location.assign(url)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not start Google login')
    }
  }

  async function runSync() {
    setSyncBusy(true)
    setSyncMsg('')
    setErr('')
    try {
      const body: { user_id?: number; only_known_contacts: boolean } = {
        only_known_contacts: onlyKnownContacts,
      }
      if (isAdmin && filterUserId) body.user_id = filterUserId
      const res = await apiFetch<{ results: { account_id: number; ok?: boolean; inserted?: number; error?: string }[] }>(
        '/api/gmail/sync',
        { method: 'POST', json: body },
      )
      const parts = (res.results || []).map(
        (r) => `account ${r.account_id}: ${r.ok ? `${r.inserted ?? 0} new` : r.error || 'failed'}`,
      )
      setSyncMsg(parts.join(' · ') || 'Done')
      await loadThreads()
      if (selectedId) {
        const params = new URLSearchParams()
        if (isAdmin && filterUserId) params.set('user_id', String(filterUserId))
        params.set('only_known_contacts', onlyKnownContacts ? 'true' : 'false')
        const q = params.toString() ? `?${params.toString()}` : ''
        const d = await apiFetch<{ thread: ThreadRow; messages: MsgRow[] }>(`/api/gmail/threads/${selectedId}${q}`)
        setDetail(d)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncBusy(false)
    }
  }

  return (
    <div className="page-wide gmail-inbox-page">
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Gmail — Actions inbox</h2>
        <p className="subh muted" style={{ marginTop: 0 }}>
          Connect each teammate&apos;s Gmail, then sync to aggregate threads here. Tokens stay on the server.
        </p>
        {err ? (
          <p className="subh" style={{ color: 'var(--danger, #c0392b)' }}>
            {err}
          </p>
        ) : null}
        {syncMsg ? <p className="subh muted">{syncMsg}</p> : null}
        <div className="gmail-inbox-toolbar">
          <button type="button" className="primary" onClick={() => void connectGmail()}>
            Connect Gmail
          </button>
          <button type="button" className="ghost" disabled={syncBusy} onClick={() => void runSync()}>
            {syncBusy ? 'Syncing…' : 'Sync now'}
          </button>
          <label className="gmail-inbox-filter">
            <span className="muted">Visibility</span>
            <select
              value={onlyKnownContacts ? 'known' : 'all'}
              onChange={(e) => setOnlyKnownContacts(e.target.value === 'known')}
            >
              <option value="known">Known DB contacts only</option>
              <option value="all">All mailbox emails</option>
            </select>
          </label>
          {isAdmin ? (
            <label className="gmail-inbox-filter">
              <span className="muted">SPOC / user</span>
              <select
                value={filterUserId ?? ''}
                onChange={(e) => {
                  const v = e.target.value
                  setFilterUserId(v === '' ? null : Number(v))
                  setSelectedId(null)
                  setDetail(null)
                }}
              >
                <option value="">Me ({me?.username})</option>
                {userList.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                    {u.email ? ` — ${u.email}` : ''}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
        {connections.length ? (
          <p className="subh muted" style={{ marginBottom: 0 }}>
            Connected: {connections.map((c) => c.email).join(', ')}
          </p>
        ) : (
          <p className="subh muted" style={{ marginBottom: 0 }}>
            No Gmail accounts connected for this user yet.
          </p>
        )}
      </div>

      <div className="gmail-inbox-split">
        <aside className="panel gmail-inbox-threads">
          <h3 className="subh" style={{ marginTop: 0 }}>
            Threads
          </h3>
          <ul className="gmail-thread-list">
            {threads.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className={`gmail-thread-item ${selectedId === t.id ? 'active' : ''}`}
                  onClick={() => setSelectedId(t.id)}
                >
                  <span className="gmail-thread-subj">{t.subject || '(no subject)'}</span>
                  <span className="gmail-thread-meta muted">
                    {t.connected_email} · {fmtTime(t.last_message_at)}
                  </span>
                  <span className="gmail-thread-snippet muted">{renderThreadPreview(t.snippet || '')}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="panel gmail-inbox-messages">
          {!selectedId ? (
            <p className="muted">Select a thread.</p>
          ) : !detail ? (
            <p className="muted">Loading…</p>
          ) : (
            <>
              <h3 className="subh" style={{ marginTop: 0 }}>
                {detail.thread.subject || '(no subject)'}
              </h3>
              <p className="muted subh">{detail.thread.connected_email}</p>
              <div className="gmail-msg-stack">
                {displayMessages.map((m) => (
                  <article
                    key={m.id}
                    className={`gmail-msg ${m.is_sent ? 'is-sent' : 'is-received'}`}
                  >
                    <div className="gmail-msg-hdr">
                      <strong>{m.from_email || '—'}</strong>
                      <span className="muted">{fmtTime(m.timestamp)}</span>
                      {m.thread_label ? <span className="gmail-msg-label">{m.thread_label}</span> : null}
                    </div>
                    <div className="gmail-msg-sub muted">{m.subject}</div>
                    <pre className="gmail-msg-body">{m.display_body}</pre>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
