import { lazy, Suspense, type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import AppShell from './shell/AppShell'
import LoginPage from './pages/LoginPage'

const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const CompaniesPage = lazy(() => import('./pages/CompaniesPage'))
const IntelligencePage = lazy(() => import('./pages/IntelligencePage'))
const StrategistPage = lazy(() => import('./pages/StrategistPage'))
const ActionsPage = lazy(() => import('./pages/ActionsPage'))
const TimelinePage = lazy(() => import('./pages/TimelinePage'))
const CalendarPage = lazy(() => import('./pages/CalendarPage'))
const HistoryPage = lazy(() => import('./pages/HistoryPage'))
const AdvisorPage = lazy(() => import('./pages/AdvisorPage'))
const BulkImportPage = lazy(() => import('./pages/BulkImportPage'))
const GmailInboxPage = lazy(() => import('./pages/GmailInboxPage'))

function SuspenseWrap({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="page-wide muted">Loading…</div>}>{children}</Suspense>
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth()
  if (!ready) return <div className="page-wide muted">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route
          path="/dashboard"
          element={
            <SuspenseWrap>
              <DashboardPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/companies"
          element={
            <SuspenseWrap>
              <CompaniesPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/intelligence"
          element={
            <SuspenseWrap>
              <IntelligencePage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/strategist"
          element={
            <SuspenseWrap>
              <StrategistPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/actions"
          element={
            <SuspenseWrap>
              <ActionsPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/timeline"
          element={
            <SuspenseWrap>
              <TimelinePage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/calendar"
          element={
            <SuspenseWrap>
              <CalendarPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/pipeline"
          element={<Navigate to="/dashboard" replace />}
        />
        <Route
          path="/spoc"
          element={<Navigate to="/dashboard" replace />}
        />
        <Route
          path="/history"
          element={
            <SuspenseWrap>
              <HistoryPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/advisor"
          element={
            <SuspenseWrap>
              <AdvisorPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/bulk-import"
          element={
            <SuspenseWrap>
              <BulkImportPage />
            </SuspenseWrap>
          }
        />
        <Route
          path="/gmail-inbox"
          element={
            <SuspenseWrap>
              <GmailInboxPage />
            </SuspenseWrap>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}
