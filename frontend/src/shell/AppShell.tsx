import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useSelection, type CompanySource } from '../state/SelectionContext'

function clearedWorkspace() {
  return {
    companyId: null as number | null,
    contactId: null as number | null,
    advisorCompanyId: null as number | null,
    sessionId: null as number | null,
  }
}

const NAV = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/companies', label: 'Companies' },
  { to: '/intelligence', label: 'Intelligence' },
  { to: '/strategist', label: 'Strategist' },
  { to: '/actions', label: 'Actions' },
  { to: '/timeline', label: 'Timeline' },
  { to: '/calendar', label: 'Calendar' },
  { to: '/pipeline', label: 'Pipeline' },
  { to: '/spoc', label: 'SPOC' },
  { to: '/history', label: 'History' },
  { to: '/advisor', label: 'Advisor' },
  { to: '/bulk-import', label: 'Bulk import' },
]

const THEME_KEY = 'lh2_theme'

export default function AppShell() {
  const { user, logout } = useAuth()
  const { selection, setSelection, clearWorkspace } = useSelection()
  const nav = useNavigate()
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    try {
      const t = localStorage.getItem(THEME_KEY)
      return t === 'light' ? 'light' : 'dark'
    } catch {
      return 'dark'
    }
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      /* ignore storage errors */
    }
  }, [theme])

  return (
    <div className="app-frame">
      <aside className="side-nav">
        <div className="side-brand">LH2</div>
        <nav>
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <div className="side-user">{user?.username}</div>
          <button type="button" className="ghost sm" onClick={logout}>
            Log out
          </button>
        </div>
      </aside>
      <div className="frame-main">
        <header className="sel-strip">
          <label className="inline">
            DB{' '}
            <select
              value={selection.source}
              onChange={(e) =>
                setSelection({ source: e.target.value as CompanySource, ...clearedWorkspace() })
              }
            >
              <option value="independent">Independent</option>
              <option value="media">Media houses</option>
            </select>
          </label>
          {selection.companyId != null && (
            <span className="sel-pill">
              Company #{selection.companyId}
              {selection.contactId != null && ` · Contact #${selection.contactId}`}
            </span>
          )}
          <button type="button" className="ghost sm" onClick={() => nav('/companies')}>
            Pick company
          </button>
          <button type="button" className="ghost sm" onClick={clearWorkspace}>
            Clear workspace
          </button>
          <button
            type="button"
            className="ghost sm"
            onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          >
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </button>
        </header>
        <main className="frame-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
