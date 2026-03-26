import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch, getToken, streamStrategist, type ChatMessage, type Contact, type User } from '../api/client'
import { apiLoadingBegin, apiLoadingEnd } from '../api/loadingBus'
import { useSelection } from '../state/SelectionContext'
import { useMeta } from '../state/MetaContext'
import { MarkdownLazy } from '../ui/MarkdownLazy'

type Workspace = {
  company: Record<string, unknown>
  contacts: Contact[]
  spoc_contacts: Record<string, unknown>[]
}

type ContactsBoardRow = {
  id?: number
  name: string
  role: string
  company: string
  linkedin_url: string
  email: string
  phone: string
  status: string
}

type RecipientOption =
  | { kind: 'company'; value: string; label: string }
  | { kind: 'contact'; value: string; label: string; contactId: number }
  | { kind: 'row'; value: string; label: string; row: ContactsBoardRow }

export default function StrategistPage() {
  const { selection, setSelection } = useSelection()
  const meta = useMeta()
  const cid = selection.companyId
  const [ws, setWs] = useState<Workspace | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [senderId, setSenderId] = useState<number | null>(null)
  const [contactId, setContactId] = useState<number | null>(selection.contactId)
  const [productKey, setProductKey] = useState('accelerate')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<'chat' | 'plan'>('chat')
  const [streamText, setStreamText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [pendingPlan, setPendingPlan] = useState<Record<string, unknown>[] | null>(null)
  const [recipientResearch, setRecipientResearch] = useState<Record<string, unknown> | null>(null)
  const [researchBusy, setResearchBusy] = useState(false)
  const [addDraftText, setAddDraftText] = useState('')
  const [addDraftBusy, setAddDraftBusy] = useState(false)
  const [parseMulti, setParseMulti] = useState(false)
  const [gmailBusy, setGmailBusy] = useState(false)
  const [gmailMsg, setGmailMsg] = useState('')
  const [boardRows, setBoardRows] = useState<ContactsBoardRow[]>([])
  const [fromExtraction, setFromExtraction] = useState(false)
  const [contactsBusy, setContactsBusy] = useState('')
  const [lookupName, setLookupName] = useState('')
  const [lookupRole, setLookupRole] = useState('')
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())

  const boardKey = (r: ContactsBoardRow) => `${(r.name || '').trim().toLowerCase()}|${(r.linkedin_url || '').trim()}`

  const loadWorkspace = useCallback(async (opts?: { silent?: boolean }) => {
    if (!cid) {
      setWs(null)
      return
    }
    const data = await apiFetch<Workspace>(
      `/api/companies/${cid}/workspace`,
      opts?.silent === true ? { silent: true } : {},
    )
    setWs(data)
  }, [cid])

  const loadContactsBoard = useCallback(async (opts?: { silent?: boolean }) => {
    if (!cid) return
    const q = new URLSearchParams({ company_id: String(cid) })
    if (selection.source === 'media' && selection.advisorCompanyId != null) {
      q.set('advisor_company_id', String(selection.advisorCompanyId))
    }
    const r = await apiFetch<{ rows: ContactsBoardRow[]; from_extraction: boolean }>(
      `/api/strategist/contacts-board?${q}`,
      opts?.silent === true ? { silent: true } : {},
    )
    setBoardRows(r.rows || [])
    setFromExtraction(Boolean(r.from_extraction))
  }, [cid, selection.source, selection.advisorCompanyId])

  useEffect(() => {
    if (!cid) {
      setBoardRows([])
      setFromExtraction(false)
      return
    }
    loadContactsBoard().catch(() => setBoardRows([]))
  }, [cid, loadContactsBoard])

  useEffect(() => {
    apiFetch<User[]>('/api/users', { silent: true }).then((u) => {
      setUsers(u)
      if (!senderId && u.length) setSenderId(u[0].id)
    })
  }, [senderId])

  useEffect(() => {
    setContactId(selection.contactId)
  }, [selection.contactId])

  useEffect(() => {
    if (!cid) {
      setWs(null)
      return
    }
    loadWorkspace()
      .catch((e) => setErr(e instanceof Error ? e.message : 'Load failed'))
  }, [cid, loadWorkspace])

  const spocMaps = useMemo(() => {
    const spoc_email_by_parent: Record<number, string> = {}
    const spoc_li_by_parent: Record<number, string> = {}
    const spoc_name_by_parent: Record<number, string> = {}
    const spoc_role_by_parent: Record<number, string> = {}
    const spoc_name_by_email: Record<string, string> = {}
    const spoc_role_by_email: Record<string, string> = {}
    for (const sc of ws?.spoc_contacts || []) {
      const pid = sc.spoc_contact_id as number
      const em = String(sc.email || '').trim()
      const nm = String(sc.name || '').trim()
      const role = String(sc.role || '').trim()
      const li = String(sc.linkedin_url || '').trim()
      if (pid && nm && !spoc_name_by_parent[pid]) spoc_name_by_parent[pid] = nm
      if (pid && role && !spoc_role_by_parent[pid]) spoc_role_by_parent[pid] = role
      if (pid && em && !spoc_email_by_parent[pid]) spoc_email_by_parent[pid] = em
      if (pid && li && !spoc_li_by_parent[pid]) spoc_li_by_parent[pid] = li
      if (em && nm) {
        spoc_name_by_email[em] = nm
        spoc_role_by_email[em] = role
      }
    }
    return {
      spoc_email_by_parent,
      spoc_li_by_parent,
      spoc_name_by_parent,
      spoc_role_by_parent,
      spoc_name_by_email,
      spoc_role_by_email,
    }
  }, [ws])

  const recipientOptions = useMemo<RecipientOption[]>(() => {
    const opts: RecipientOption[] = [{ kind: 'company', value: '', label: 'Company level' }]
    const seenContact = new Set<number>()
    for (const c of ws?.contacts || []) {
      seenContact.add(c.id)
      opts.push({
        kind: 'contact',
        value: `c:${c.id}`,
        contactId: c.id,
        label: (c.name || c.email || `Contact #${c.id}`).trim(),
      })
    }
    const norm = (s: string) => (s || '').trim().toLowerCase()
    for (const r of boardRows || []) {
      const rName = norm(r.name)
      const rEmail = norm(r.email)
      const rLi = norm(r.linkedin_url)
      const matched = (ws?.contacts || []).find((c) => {
        const cName = norm(c.name || '')
        const cEmail = norm(c.email || '')
        const cLi = norm(c.linkedin || '')
        return (!!rName && cName === rName) || (!!rEmail && cEmail === rEmail) || (!!rLi && cLi === rLi)
      })
      if (matched && !seenContact.has(matched.id)) {
        seenContact.add(matched.id)
        opts.push({
          kind: 'contact',
          value: `c:${matched.id}`,
          contactId: matched.id,
          label: (matched.name || matched.email || `Contact #${matched.id}`).trim(),
        })
      } else if (!matched) {
        opts.push({
          kind: 'row',
          value: `r:${rName}|${rEmail}|${rLi}`,
          label: r.name || 'Extracted contact',
          row: r,
        })
      }
    }
    return opts
  }, [ws, boardRows])

  const ctCtx = useMemo(() => {
    const c = ws?.contacts.find((x) => x.id === contactId) || null
    if (!c) return null
    const ctx = { ...c }
    const em = (ctx.email || '').trim()
    if (!em) ctx.email = spocMaps.spoc_email_by_parent[ctx.id] || ''
    if (!(ctx.linkedin || '').trim()) ctx.linkedin = spocMaps.spoc_li_by_parent[ctx.id] || ''
    let name = (ctx.name || '').trim()
    const em2 = (ctx.email || '').trim()
    if (!name || name.includes('@') || name.includes('.com')) {
      name = spocMaps.spoc_name_by_email[em2] || spocMaps.spoc_name_by_parent[ctx.id] || name
      ctx.name = name
    }
    if (!(ctx.role || '').trim() && em2) ctx.role = spocMaps.spoc_role_by_email[em2] || spocMaps.spoc_role_by_parent[ctx.id] || ''
    return ctx
  }, [ws, contactId, spocMaps])

  useEffect(() => {
    if (!cid) return
    apiFetch<{ session_id: number }>(`/api/companies/${cid}/session`, {
      method: 'POST',
      silent: true,
      json: { contact_id: contactId ?? null },
    }).then((r) => setSelection({ sessionId: r.session_id }))
  }, [cid, contactId, setSelection])

  const sid = selection.sessionId

  useEffect(() => {
    if (!sid) {
      setMessages([])
      return
    }
    apiFetch<{ messages: ChatMessage[] }>(`/api/sessions/${sid}/messages`, { silent: true }).then((m) =>
      setMessages(m.messages),
    )
  }, [sid])

  const senderEmail = users.find((u) => u.id === senderId)?.email || ''

  function parseDraft(content: string) {
    const raw = (content || '').trim()
    if (!raw) return { subject: '', body: '' }
    const lines = raw.split('\n')
    let subject = ''
    let bodyLines = lines
    for (let i = 0; i < lines.length; i++) {
      const ln = lines[i]?.trim() || ''
      if (ln.toLowerCase().startsWith('subject:')) {
        subject = ln.split(':', 2).slice(1).join(':').trim()
        bodyLines = lines.slice(i + 1)
        break
      }
    }
    return { subject, body: bodyLines.join('\n').trim() }
  }

  function looksLikeDraft(content: string) {
    const v = (content || '').trim()
    if (!v) return false
    const l = v.toLowerCase()
    return l.includes('subject:') || l.includes('dear ') || l.includes('hi ') || l.includes('hello ')
  }

  async function onSend(e: FormEvent) {
    e.preventDefault()
    if (!sid || !input.trim() || busy) return
    const text = input.trim()
    setInput('')
    setBusy(true)
    setStreamText('')
    setErr('')
    setMessages((m) => [...m, { id: -1, role: 'user', content: text }])
    try {
      let acc = ''
      await streamStrategist(sid, text, mode, (ch) => {
        acc += ch
        setStreamText(acc)
      }, {
        product_key: productKey,
        sender_email: senderEmail || null,
        recipient_research: recipientResearch,
      })
      const m = await apiFetch<{ messages: ChatMessage[] }>(`/api/sessions/${sid}/messages`, {
        silent: true,
      })
      setMessages(m.messages)
      setStreamText('')
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Chat failed')
      setMessages((m) => m.filter((x) => x.id !== -1))
    } finally {
      setBusy(false)
      setStreamText('')
    }
  }

  async function fullPlan() {
    if (!sid) return
    setBusy(true)
    setErr('')
    try {
      const plan = await apiFetch<{ plan: Record<string, unknown>[] }>(`/api/sessions/${sid}/outreach-plan`, {
        method: 'POST',
        silent: true,
        json: {
          sender_name: users.find((u) => u.id === senderId)?.username || '',
          sender_email: senderEmail || null,
          product_key: productKey,
          recipient_research: recipientResearch,
        },
      })
      const steps = plan.plan || []
      if (!steps.length) {
        setErr(
          'No plan steps returned. Save deep research on Intelligence for this company, then try again.',
        )
        setPendingPlan(null)
        return
      }
      setPendingPlan(steps)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Plan failed')
    } finally {
      setBusy(false)
    }
  }

  async function downloadPlanIcs() {
    if (!sid || !pendingPlan?.length) return
    apiLoadingBegin()
    try {
      const token = getToken()
      const res = await fetch(`/api/sessions/${sid}/outreach-plan/ics`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          steps: pendingPlan,
          title: `LH2 — ${String(ws?.company?.domain || 'plan')}`,
        }),
      })
      if (!res.ok) return
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'lh2_plan.ics'
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      apiLoadingEnd()
    }
  }

  async function runRecipientResearch(forceRefresh = false) {
    if (!contactId) return
    setResearchBusy(true)
    setErr('')
    try {
      const r = await apiFetch<{ research: Record<string, unknown> }>(
        `/api/contacts/${contactId}/recipient-research`,
        { method: 'POST', silent: true, json: { force_refresh: forceRefresh } },
      )
      setRecipientResearch(r.research || null)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Research failed')
    } finally {
      setResearchBusy(false)
    }
  }

  async function runExtractContacts() {
    if (!cid || !sid) return
    setContactsBusy('ex')
    setErr('')
    try {
      await apiFetch('/api/strategist/extract-contacts', {
        method: 'POST',
        silent: true,
        json: {
          company_id: cid,
          advisor_company_id: selection.source === 'media' ? selection.advisorCompanyId : null,
          session_id: sid,
        },
      })
      await loadContactsBoard({ silent: true })
      await loadWorkspace({ silent: true })
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Extract failed')
    } finally {
      setContactsBusy('')
    }
  }

  async function addPersonLookup() {
    if (!cid || !lookupName.trim()) return
    setContactsBusy('lk')
    setErr('')
    try {
      await apiFetch('/api/strategist/add-contact-lookup', {
        method: 'POST',
        silent: true,
        json: {
          company_id: cid,
          advisor_company_id: selection.source === 'media' ? selection.advisorCompanyId : null,
          name: lookupName.trim(),
          role: lookupRole.trim(),
        },
      })
      setLookupName('')
      setLookupRole('')
      await loadContactsBoard({ silent: true })
      await loadWorkspace({ silent: true })
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Lookup failed')
    } finally {
      setContactsBusy('')
    }
  }

  async function enrichSelectedContacts() {
    if (!cid) return
    const rows = boardRows.filter((r) => selectedRows.has(boardKey(r)))
    if (!rows.length) return
    setContactsBusy('en')
    setErr('')
    try {
      await apiFetch('/api/strategist/enrich-contacts', {
        method: 'POST',
        silent: true,
        json: {
          company_id: cid,
          advisor_company_id: selection.source === 'media' ? selection.advisorCompanyId : null,
          rows,
        },
      })
      await loadContactsBoard({ silent: true })
      await loadWorkspace({ silent: true })
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Enrichment failed')
    } finally {
      setContactsBusy('')
    }
  }

  async function deleteSelectedContacts() {
    if (!cid) return
    const rows = boardRows.filter((r) => selectedRows.has(boardKey(r)))
    if (!rows.length) return
    setContactsBusy('del')
    setErr('')
    try {
      await apiFetch('/api/strategist/delete-contacts', {
        method: 'POST',
        silent: true,
        json: {
          company_id: cid,
          advisor_company_id: selection.source === 'media' ? selection.advisorCompanyId : null,
          rows,
        },
      })
      setSelectedRows(new Set())
      await loadContactsBoard({ silent: true })
      await loadWorkspace({ silent: true })
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Delete failed')
    } finally {
      setContactsBusy('')
    }
  }

  async function onRecipientChange(value: string) {
    if (!value) {
      setContactId(null)
      setSelection({ contactId: null })
      return
    }
    const opt = recipientOptions.find((o) => o.value === value)
    if (!opt) return
    if (opt.kind === 'contact') {
      setContactId(opt.contactId)
      setSelection({ contactId: opt.contactId })
      return
    }
    if (opt.kind === 'row' && cid) {
      try {
        const r = await apiFetch<{ contact_id: number }>('/api/strategist/ensure-contact', {
          method: 'POST',
          silent: true,
          json: {
            company_id: cid,
            advisor_company_id: selection.source === 'media' ? selection.advisorCompanyId : null,
            name: opt.row.name,
            role: opt.row.role === '—' ? '' : opt.row.role,
            email: opt.row.email === '—' ? '' : opt.row.email,
            linkedin_url: opt.row.linkedin_url === '—' ? '' : opt.row.linkedin_url,
          },
        })
        await loadWorkspace({ silent: true })
        setContactId(r.contact_id)
        setSelection({ contactId: r.contact_id })
      } catch (ex) {
        setErr(ex instanceof Error ? ex.message : 'Could not select contact')
      }
    }
  }

  async function addDraftToTimeline(draftText: string) {
    if (!sid || !senderId || !draftText.trim()) return
    setAddDraftBusy(true)
    setErr('')
    try {
      await apiFetch(`/api/sessions/${sid}/strategist-draft-to-actions`, {
        method: 'POST',
        silent: true,
        json: {
          sender_user_id: senderId,
          draft_content: draftText.trim(),
          mode: parseMulti ? 'multi_sequence' : 'new_sequence',
          product_key: productKey,
          parse_multi: parseMulti,
          channel: 'Email',
        },
      })
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Add to timeline failed')
    } finally {
      setAddDraftBusy(false)
    }
  }

  async function addChatDraftToTimeline() {
    await addDraftToTimeline(addDraftText)
  }

  function fillDraftFromLastAssistant() {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        setAddDraftText(messages[i].content)
        return
      }
    }
  }

  async function saveDraftToGmail(content: string, stepIndex?: number) {
    if (!content.trim()) {
      setErr('Draft content is required to save Gmail draft')
      return
    }
    setGmailBusy(true)
    setGmailMsg('')
    setErr('')
    try {
      const out = await apiFetch<{ draft_id: string }>('/api/gmail/draft', {
        method: 'POST',
        silent: true,
        json: {
          account_email: senderEmail || null,
          to_email: ctCtx?.email || '',
          content: content.trim(),
          product_key: productKey,
          step_index: stepIndex ?? null,
          lead_brand: String(ws?.company?.name || ws?.company?.domain || ''),
        },
      })
      setGmailMsg(`Saved to Gmail drafts (${out.draft_id})`)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Save to Gmail draft failed')
    } finally {
      setGmailBusy(false)
    }
  }

  async function commitPlan() {
    if (!sid || !pendingPlan?.length || !senderId) return
    setBusy(true)
    try {
      await apiFetch(`/api/sessions/${sid}/outreach-plan/commit`, {
        method: 'POST',
        silent: true,
        json: { steps: pendingPlan, product_key: productKey, sender_user_id: senderId },
      })
      setPendingPlan(null)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Commit failed')
    } finally {
      setBusy(false)
    }
  }

  if (!cid) {
    return (
      <div className="empty-state">
        <p>Select a company from <strong>Companies</strong> to use the strategist.</p>
      </div>
    )
  }

  const products = meta?.products?.length ? meta.products : [{ key: 'accelerate', label: 'Accelerate' }]

  return (
    <div className="page-wide strategist">
      <header className="intel-hero strategist-header">
        <div className="intel-hero-text">
          <p className="intel-eyebrow">Strategist</p>
          <h1>{(ws?.company?.name as string) || String(ws?.company?.domain || 'Workspace')}</h1>
          <p className="intel-domain muted">{String(ws?.company?.domain || '')}</p>
        </div>
      </header>
      {err && <p className="error">{err}</p>}
      {gmailMsg && <p className="ok">{gmailMsg}</p>}

      <div className="strategist-shell">
        <aside className="strategist-rail" aria-label="Workspace controls">
          <section className="panel">
            <h2>Routing</h2>
            <div className="strategist-toolbar">
              <label>
                From
                <select
                  value={senderId ?? ''}
                  onChange={(e) => setSenderId(Number(e.target.value))}
                  aria-label="Sender user"
                >
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.email || u.username}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                To
                <select
                  value={contactId != null ? `c:${contactId}` : ''}
                  onChange={(e) => void onRecipientChange(e.target.value)}
                  aria-label="Recipient contact"
                >
                  {recipientOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {ctCtx && (
              <p className="muted small strategist-rail-contact">
                <strong>{ctCtx.name}</strong>
                {ctCtx.role ? ` · ${ctCtx.role}` : ''}
                <br />
                {ctCtx.email || 'No email'}
              </p>
            )}
          </section>

          <section className="panel">
            <h2>Product</h2>
            <div className="product-row">
              {products.map((p) => (
                <label key={p.key} className="radio-pill">
                  <input
                    type="radio"
                    name="pk"
                    checked={productKey === p.key}
                    onChange={() => setProductKey(p.key)}
                  />
                  {p.label}
                </label>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Quick prompts</h2>
            <div className="quick-actions quick-actions-col">
              <button
                type="button"
                className="secondary sm"
                onClick={() => setInput('Draft a LinkedIn connection request under 300 characters.')}
              >
                LinkedIn
              </button>
              <button
                type="button"
                className="secondary sm"
                onClick={() => setInput('Draft the initial outreach email with subject line.')}
              >
                Email
              </button>
              <button type="button" className="secondary sm" onClick={fullPlan} disabled={busy || !sid}>
                Full plan
              </button>
              {contactId != null && (
                <button
                  type="button"
                  className="secondary sm"
                  onClick={() => runRecipientResearch(false)}
                  disabled={busy || researchBusy}
                >
                  {researchBusy ? 'Research…' : 'Recipient research'}
                </button>
              )}
              {contactId != null && recipientResearch && (
                <button
                  type="button"
                  className="ghost sm"
                  onClick={() => runRecipientResearch(true)}
                  disabled={researchBusy}
                >
                  Refresh research
                </button>
              )}
            </div>
          </section>

          {recipientResearch && (
            <section className="panel strategist-research">
              <h2>Recipient context</h2>
              <div className="plan-step-draft">
                <MarkdownLazy
                  text={
                    typeof recipientResearch.brief === 'string'
                      ? recipientResearch.brief
                      : JSON.stringify(recipientResearch, null, 2)
                  }
                />
              </div>
              <p className="muted small">Passed into chat &amp; plan requests for personalization.</p>
            </section>
          )}

          {sid && senderId && (
            <section className="panel strategist-add-draft">
              <h2>To timeline</h2>
              <p className="muted small">
                Multiple emails split automatically when the draft has lines like <code>Email 1:</code>,{' '}
                <code>Email 2:</code>, or <code>Email 1 — …</code>. Each step saves as subject + body only.
              </p>
              <div className="btn-row">
                <button type="button" className="secondary sm" onClick={fillDraftFromLastAssistant}>
                  Last reply
                </button>
                <label className="chk-inline">
                  <input type="checkbox" checked={parseMulti} onChange={(e) => setParseMulti(e.target.checked)} />
                  Require multi-email (error if fewer than 2 sections)
                </label>
              </div>
              <textarea
                rows={6}
                value={addDraftText}
                onChange={(e) => setAddDraftText(e.target.value)}
                placeholder="Draft…"
              />
              <button
                type="button"
                className="primary sm"
                onClick={addChatDraftToTimeline}
                disabled={addDraftBusy || !addDraftText.trim()}
              >
                {addDraftBusy ? '…' : 'Add to timeline'}
              </button>
            </section>
          )}

        </aside>

        <div className="strategist-main">
          {cid && (
            <section className="panel strategist-contacts-panel">
              <div className="intel-card-head">
                <h2>Contacts from research</h2>
                <div className="btn-row">
                  <button
                    type="button"
                    className="secondary sm"
                    disabled={!sid || contactsBusy === 'ex'}
                    onClick={runExtractContacts}
                  >
                    {contactsBusy === 'ex' ? '…' : 'Extract & find LinkedIn'}
                  </button>
                  <button
                    type="button"
                    className="secondary sm"
                    disabled={contactsBusy === 'en' || selectedRows.size === 0}
                    onClick={enrichSelectedContacts}
                  >
                    {contactsBusy === 'en' ? '…' : 'Extract SignalHire details'}
                  </button>
                  <button
                    type="button"
                    className="ghost sm"
                    disabled={contactsBusy === 'del' || selectedRows.size === 0}
                    onClick={deleteSelectedContacts}
                  >
                    {contactsBusy === 'del' ? '…' : 'Delete selected'}
                  </button>
                </div>
              </div>
              <p className="muted small">
                {fromExtraction
                  ? 'From SPOC tables in deep research (saved on this company).'
                  : 'Company contacts from the database. Run extract to pull from research.'}
              </p>
              <div className="strategist-lookup-row">
                <input
                  type="text"
                  className="intel-cmd-input"
                  placeholder="Name to look up"
                  value={lookupName}
                  onChange={(e) => setLookupName(e.target.value)}
                  aria-label="Person name lookup"
                />
                <input
                  type="text"
                  className="intel-cmd-input"
                  placeholder="Role (optional)"
                  value={lookupRole}
                  onChange={(e) => setLookupRole(e.target.value)}
                  aria-label="Person role"
                />
                <button
                  type="button"
                  className="primary sm"
                  disabled={contactsBusy === 'lk' || !lookupName.trim()}
                  onClick={addPersonLookup}
                >
                  {contactsBusy === 'lk' ? '…' : 'Add lookup'}
                </button>
              </div>
              <div className="lh2-table-wrap strategist-contacts-wrap">
                <table className="lh2-data-table">
                  <thead>
                    <tr>
                      <th>
                        <input
                          type="checkbox"
                          aria-label="Select all rows"
                          checked={boardRows.length > 0 && selectedRows.size === boardRows.length}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedRows(new Set(boardRows.map((r) => boardKey(r))))
                            } else {
                              setSelectedRows(new Set())
                            }
                          }}
                        />
                      </th>
                      <th>Name</th>
                      <th>Role</th>
                      <th>Company</th>
                      <th>LinkedIn</th>
                      <th>Email</th>
                      <th>Phone</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {boardRows.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="muted">
                          No rows yet.
                        </td>
                      </tr>
                    ) : (
                      boardRows.map((r, i) => (
                        <tr key={`${r.name}-${i}`}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Select ${r.name}`}
                              checked={selectedRows.has(boardKey(r))}
                              onChange={(e) => {
                                const k = boardKey(r)
                                setSelectedRows((prev) => {
                                  const n = new Set(prev)
                                  if (e.target.checked) n.add(k)
                                  else n.delete(k)
                                  return n
                                })
                              }}
                            />
                          </td>
                          <td>{r.name}</td>
                          <td>{r.role}</td>
                          <td>{r.company}</td>
                          <td>
                            {r.linkedin_url && r.linkedin_url !== '—' && r.linkedin_url.startsWith('http') ? (
                              <a href={r.linkedin_url} target="_blank" rel="noopener noreferrer">
                                Profile
                              </a>
                            ) : (
                              <span className="lh2-dt-readonly">{r.linkedin_url}</span>
                            )}
                          </td>
                          <td>{r.email}</td>
                          <td>{r.phone}</td>
                          <td>{r.status}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <div className="messages strategist-msgs">
            {messages.map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div className="msg-role">{m.role}</div>
                {m.role === 'assistant' && looksLikeDraft(m.content) ? (
                  (() => {
                    const parsed = parseDraft(m.content)
                    return (
                      <div className="strategist-draft-card">
                        {parsed.subject && <div className="draft-subject"><strong>Subject:</strong> {parsed.subject}</div>}
                        <pre className="draft-body">{parsed.body || m.content}</pre>
                        <div className="btn-row">
                          <button type="button" className="secondary sm" onClick={() => setAddDraftText(m.content)}>
                            Use in timeline box
                          </button>
                          <button type="button" className="primary sm" onClick={() => void addDraftToTimeline(m.content)} disabled={addDraftBusy}>
                            {addDraftBusy ? '…' : 'Add to timeline'}
                          </button>
                          <button
                            type="button"
                            className="ghost sm"
                            onClick={() => void saveDraftToGmail(m.content)}
                            disabled={gmailBusy}
                          >
                            {gmailBusy ? '…' : 'Save Gmail draft'}
                          </button>
                        </div>
                      </div>
                    )
                  })()
                ) : (
                  <MarkdownLazy text={m.content} />
                )}
              </div>
            ))}
            {streamText && (
              <div className="msg assistant streaming">
                <div className="msg-role">assistant</div>
                <MarkdownLazy text={streamText} />
              </div>
            )}
          </div>

          {pendingPlan && pendingPlan.length > 0 && (
            <section className="panel plan-preview strategist-main-plan">
              <div className="intel-card-head">
                <h2>Generated Plan ({pendingPlan.length})</h2>
                <div className="btn-row">
                  <button type="button" className="primary sm" onClick={commitPlan} disabled={busy}>
                    Add full plan to timeline
                  </button>
                  <button type="button" className="secondary sm" onClick={downloadPlanIcs}>
                    .ics
                  </button>
                  <button type="button" className="ghost sm" onClick={() => setPendingPlan(null)}>
                    Discard
                  </button>
                </div>
              </div>
              <ol className="plan-preview-list">
                {pendingPlan.map((st, i) => (
                  <li key={i}>
                    <details open={i < 2}>
                      <summary>
                        Step {String(st.step)} · {String(st.channel)}
                      </summary>
                      <div className="muted small">{String(st.description || '')}</div>
                      {st.content_draft != null && String(st.content_draft).trim() && (
                        (() => {
                          const raw = String(st.content_draft)
                          const parsed = parseDraft(raw)
                          return (
                            <div className="plan-step-draft strategist-draft-card">
                              {parsed.subject && <div className="draft-subject"><strong>Subject:</strong> {parsed.subject}</div>}
                              <pre className="draft-body">{parsed.body || raw}</pre>
                              <div className="btn-row">
                                <button type="button" className="secondary sm" onClick={() => setAddDraftText(raw)}>
                                  Use in timeline box
                                </button>
                                <button type="button" className="primary sm" onClick={() => void addDraftToTimeline(raw)} disabled={addDraftBusy}>
                                  {addDraftBusy ? '…' : 'Add step to timeline'}
                                </button>
                                <button
                                  type="button"
                                  className="ghost sm"
                                  onClick={() => void saveDraftToGmail(raw, Number(st.step || i + 1))}
                                  disabled={gmailBusy}
                                >
                                  {gmailBusy ? '…' : 'Save Gmail draft'}
                                </button>
                              </div>
                            </div>
                          )
                        })()
                      )}
                    </details>
                  </li>
                ))}
              </ol>
            </section>
          )}

          <form className="chat-form" onSubmit={onSend}>
            <div className="mode-inline">
              <label>
                <input type="radio" checked={mode === 'chat'} onChange={() => setMode('chat')} />
                Chat
              </label>
              <label>
                <input type="radio" checked={mode === 'plan'} onChange={() => setMode('plan')} />
                Plan stream
              </label>
            </div>
            <textarea
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Message strategist…"
              disabled={!sid || busy}
            />
            <button type="submit" className="primary" disabled={!sid || busy || !input.trim()}>
              {busy ? '…' : 'Send'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
