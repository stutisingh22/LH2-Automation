import { type FormEvent, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { MarkdownLazy } from '../ui/MarkdownLazy'

type Msg = { role: string; content: string }

export default function AdvisorPage() {
  const { user } = useAuth()
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [convs, setConvs] = useState<Record<string, unknown>[]>([])

  useEffect(() => {
    apiFetch<Record<string, unknown>[]>('/api/advisor-chat/conversations').then(setConvs).catch(() => {})
  }, [])

  async function loadConv(id: number) {
    const row = await apiFetch<{ messages: Msg[] }>(`/api/advisor-chat/conversations/${id}`)
    setMessages(row.messages || [])
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!input.trim() || busy) return
    const next = [...messages, { role: 'user', content: input.trim() }]
    setInput('')
    setBusy(true)
    setErr('')
    try {
      const r = await apiFetch<{ messages: Msg[] }>('/api/advisor-chat/turn', {
        method: 'POST',
        json: { messages: next, sender_email: user?.email || null },
      })
      setMessages(r.messages || [])
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-wide">
      <h1>Advisor</h1>
      <p className="muted">Pipeline-wide chat (same AI backend as Streamlit).</p>
      {convs.length > 0 && (
        <select
          className="search"
          defaultValue=""
          onChange={(e) => e.target.value && loadConv(Number(e.target.value))}
        >
          <option value="">Load conversation…</option>
          {convs.map((c) => (
            <option key={c.id as number} value={c.id as number}>
              {String(c.title)}
            </option>
          ))}
        </select>
      )}
      {err && <p className="error">{err}</p>}
      <div className="messages strategist-msgs">
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">{m.role}</div>
            <MarkdownLazy text={m.content} />
          </div>
        ))}
      </div>
      <form className="chat-form" onSubmit={onSubmit}>
        <textarea
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask for suggestions, plans, or advice…"
        />
        <button type="submit" className="primary" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
