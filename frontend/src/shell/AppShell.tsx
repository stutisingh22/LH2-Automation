import { useEffect, useState, type MouseEvent } from 'react'
import { apiFetch } from '../api/client'
import { createPortal } from 'react-dom'
import {
  Building2,
  Calendar,
  CalendarClock,
  ChevronsLeft,
  ChevronsRight,
  History,
  LayoutDashboard,
  ListTodo,
  LogOut,
  MessageSquareText,
  Sparkles,
  Target,
  type LucideIcon,
  Upload,
  X,
  Mail,
} from 'lucide-react'
import { NavLink, Outlet, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { mergeSelectionIntoSearchString } from '../lib/workspaceUrl'
import { useSelection, type CompanySource } from '../state/SelectionContext'

function clearedWorkspace() {
  return {
    companyId: null as number | null,
    contactId: null as number | null,
    advisorCompanyId: null as number | null,
    sessionId: null as number | null,
  }
}

const NAV: { to: string; label: string; Icon: LucideIcon }[] = [
  { to: '/dashboard', label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/companies', label: 'Companies', Icon: Building2 },
  { to: '/intelligence', label: 'Intelligence', Icon: Sparkles },
  { to: '/strategist', label: 'Strategist', Icon: Target },
  { to: '/actions', label: 'Actions', Icon: ListTodo },
  { to: '/gmail-inbox', label: 'Gmail inbox', Icon: Mail },
  { to: '/timeline', label: 'Timeline', Icon: CalendarClock },
  { to: '/calendar', label: 'Calendar', Icon: Calendar },
  { to: '/history', label: 'History', Icon: History },
  { to: '/advisor', label: 'Advisor', Icon: MessageSquareText },
  { to: '/bulk-import', label: 'Bulk import', Icon: Upload },
]

const NAV_ICON_PROPS = { className: 'nav-link-icon', size: 18, strokeWidth: 1.75 } as const

const THEME_KEY = 'lh2_theme'
const SIDENAV_COLLAPSED_KEY = 'lh2_side_nav_collapsed'

type NavFlyout = { text: string; top: number; left: number } | null

export default function AppShell() {
  const { user, logout } = useAuth()
  const { selection, setSelection, clearWorkspace } = useSelection()
  const nav = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [navFlyout, setNavFlyout] = useState<NavFlyout>(null)
  const [navCollapsed, setNavCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDENAV_COLLAPSED_KEY) === '1'
    } catch {
      return false
    }
  })
  const [mediaWorkspacePill, setMediaWorkspacePill] = useState<string | null>(null)

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

  useEffect(() => {
    try {
      localStorage.setItem(SIDENAV_COLLAPSED_KEY, navCollapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [navCollapsed])

  useEffect(() => {
    const aid = selection.advisorCompanyId
    if (aid == null) {
      setMediaWorkspacePill(null)
      return
    }
    let cancelled = false
    apiFetch<{ parent_name?: string; domain?: string }>(`/api/advisor/companies/${aid}`)
      .then((row) => {
        if (cancelled) return
        const pn = (row.parent_name || '').trim() || '(No parent)'
        const dom = (row.domain || '').trim() || '—'
        setMediaWorkspacePill(`${pn} · ${dom}`)
      })
      .catch(() => {
        if (!cancelled) setMediaWorkspacePill(null)
      })
    return () => {
      cancelled = true
    }
  }, [selection.advisorCompanyId])

  function showNavFlyout(e: MouseEvent<HTMLElement>, text: string) {
    if (!navCollapsed) return
    const r = e.currentTarget.getBoundingClientRect()
    setNavFlyout({ text, top: r.top + r.height / 2, left: r.right + 10 })
  }

  function hideNavFlyout() {
    setNavFlyout(null)
  }

  return (
    <div className="app-frame">
      <aside
        id="lh2-sidebar"
        className={navCollapsed ? 'side-nav side-nav--collapsed' : 'side-nav'}
        onMouseLeave={hideNavFlyout}
      >
        <div className="side-head">
          <div className="side-brand">LH2</div>
          <button
            type="button"
            className="side-nav-toggle ghost sm"
            onClick={() => setNavCollapsed((c) => !c)}
            aria-expanded={!navCollapsed}
            aria-controls="lh2-sidebar"
            aria-label={navCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {navCollapsed ? <ChevronsRight size={18} strokeWidth={1.75} /> : <ChevronsLeft size={18} strokeWidth={1.75} />}
          </button>
        </div>
        <nav>
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
              aria-label={navCollapsed ? label : undefined}
              onMouseEnter={(e) => showNavFlyout(e, label)}
            >
              <Icon {...NAV_ICON_PROPS} aria-hidden />
              <span className="nav-link-text">{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          {!navCollapsed && <div className="side-user">{user?.username}</div>}
          <button
            type="button"
            className={navCollapsed ? 'ghost sm side-logout side-logout--icon' : 'ghost sm side-logout'}
            onClick={logout}
            aria-label="Log out"
            onMouseEnter={(e) => showNavFlyout(e, 'Log out')}
          >
            {navCollapsed ? <LogOut size={18} strokeWidth={1.75} aria-hidden /> : 'Log out'}
          </button>
        </div>
      </aside>
      {navFlyout &&
        navCollapsed &&
        createPortal(
          <div
            className="side-nav-flyout"
            style={{ top: navFlyout.top, left: navFlyout.left }}
            role="tooltip"
          >
            {navFlyout.text}
          </div>,
          document.body,
        )}
      <div className="frame-main">
        <header className="sel-strip">
          <label className="inline">
            DB{' '}
            <select
              value={selection.source}
              onChange={(e) => {
                const source = e.target.value as CompanySource
                const next = { source, ...clearedWorkspace() }
                setSelection(next)
                setSearchParams(mergeSelectionIntoSearchString(searchParams.toString(), next), {
                  replace: true,
                })
              }}
            >
              <option value="independent">Independent</option>
              <option value="media">Media houses</option>
            </select>
          </label>
          {(selection.companyId != null || selection.advisorCompanyId != null) && (
            <span className="sel-pill-wrap">
              <span className="sel-pill">
                {selection.advisorCompanyId != null && mediaWorkspacePill
                  ? mediaWorkspacePill
                  : [
                      selection.companyId != null ? `Company #${selection.companyId}` : '',
                      selection.advisorCompanyId != null ? `Advisor #${selection.advisorCompanyId}` : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                {selection.contactId != null && ` · Contact #${selection.contactId}`}
              </span>
              <button
                type="button"
                className="sel-pill-clear ghost sm"
                aria-label="Clear company and open Companies"
                onClick={() => {
                  clearWorkspace()
                  nav('/companies')
                }}
              >
                <X size={14} strokeWidth={2} aria-hidden />
              </button>
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
