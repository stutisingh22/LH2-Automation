import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function LoginPage() {
  const { user, ready, login, register } = useAuth()
  const [tab, setTab] = useState<'in' | 'up'>('in')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  if (ready && user) return <Navigate to="/dashboard" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      if (tab === 'in') await login(username.trim(), password)
      else await register(username.trim(), password, email.trim() || undefined)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">LH2</div>
        <h1>Command Center</h1>
        <p className="auth-sub">Relationship intelligence</p>
        <div className="auth-tabs">
          <button type="button" className={tab === 'in' ? 'active' : ''} onClick={() => setTab('in')}>
            Sign in
          </button>
          <button type="button" className={tab === 'up' ? 'active' : ''} onClick={() => setTab('up')}>
            Register
          </button>
        </div>
        <form onSubmit={onSubmit}>
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
          </label>
          {tab === 'up' && (
            <label>
              Email (optional)
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </label>
          )}
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={tab === 'in' ? 'current-password' : 'new-password'}
              required
            />
          </label>
          {err && <p className="error">{err}</p>}
          <button type="submit" disabled={busy} className="primary">
            {busy ? '…' : tab === 'in' ? 'Sign in' : 'Create account'}
          </button>
        </form>
      </div>
    </div>
  )
}
