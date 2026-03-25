import { apiLoadingBegin, apiLoadingEnd } from './loadingBus'

const TOKEN_KEY = 'lh2_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export type ApiFetchOptions = RequestInit & { json?: unknown; silent?: boolean }

/**
 * Global loading overlay on all routes unless opted out with `silent: true`.
 * `silent` defaults to false via destructuring when omitted.
 */
export async function apiFetch<T>(path: string, opts: ApiFetchOptions = {}): Promise<T> {
  const { json: jsonBody, silent = false, ...fetchOpts } = opts
  const useLoader = !silent
  if (useLoader) apiLoadingBegin()
  const headers: Record<string, string> = {
    ...(fetchOpts.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let body = fetchOpts.body
  if (jsonBody !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(jsonBody)
  }
  try {
    const res = await fetch(path, { ...fetchOpts, headers, body })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const j = await res.json()
        if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
      } catch {
        /* ignore */
      }
      throw new Error(detail || `HTTP ${res.status}`)
    }
    if (res.status === 204) return undefined as T
    return res.json() as Promise<T>
  } finally {
    if (useLoader) apiLoadingEnd()
  }
}

export type User = { id: number; username: string; email: string | null; role: string }

export type Company = {
  id: number
  name: string
  domain: string
  publisher_type?: string | null
  monthly_visits?: number | null
  research_status?: string | null
  extra_data?: Record<string, unknown> | null
}

export type Contact = {
  id: number
  company_id: number
  name: string
  role?: string | null
  email?: string | null
  linkedin?: string | null
  status?: string | null
}

export type ChatMessage = { id: number; role: string; content: string; created_at?: string | null }

export async function streamStrategist(
  sessionId: number,
  message: string,
  mode: 'chat' | 'plan',
  onChunk: (s: string) => void,
  extra?: {
    product_key?: string | null
    sender_email?: string | null
    recipient_research?: unknown | null
  },
): Promise<void> {
  const token = getToken()
  const res = await fetch(`/api/sessions/${sessionId}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      mode,
      product_key: extra?.product_key || null,
      sender_email: extra?.sender_email || null,
      recipient_research: extra?.recipient_research ?? null,
    }),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      const t = await res.text()
      if (t) detail = t.slice(0, 500)
    }
    throw new Error(detail)
  }
  const reader = res.body?.getReader()
  if (!reader) throw new Error('No response body')
  const dec = new TextDecoder()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    onChunk(dec.decode(value, { stream: true }))
  }
}

export async function downloadAuthorized(
  path: string,
  filename: string,
  opts: { silent?: boolean } = {},
) {
  const { silent = false } = opts
  const useLoader = !silent
  if (useLoader) apiLoadingBegin()
  try {
    const token = getToken()
    const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!res.ok) throw new Error('Download failed')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  } finally {
    if (useLoader) apiLoadingEnd()
  }
}
