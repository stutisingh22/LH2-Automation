import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { apiFetch, getToken, setToken, type User } from '../api/client'

type AuthState = {
  user: User | null
  ready: boolean
  login: (u: string, p: string) => Promise<void>
  register: (u: string, p: string, email?: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null)
      setReady(true)
      return
    }
    try {
      const me = await apiFetch<User>('/api/auth/me', { silent: true })
      setUser(me)
    } catch {
      setToken(null)
      setUser(null)
    } finally {
      setReady(true)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const login = useCallback(async (username: string, password: string) => {
    const r = await apiFetch<{ access_token: string }>('/api/auth/login', {
      method: 'POST',
      silent: true,
      json: { username, password },
    })
    setToken(r.access_token)
    await refresh()
  }, [refresh])

  const register = useCallback(
    async (username: string, password: string, email?: string) => {
      const r = await apiFetch<{ access_token: string }>('/api/auth/register', {
        method: 'POST',
        silent: true,
        json: { username, password, email: email || null },
      })
      setToken(r.access_token)
      await refresh()
    },
    [refresh],
  )

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, ready, login, register, logout }),
    [user, ready, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}
