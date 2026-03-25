import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { subscribeApiLoading } from '../api/loadingBus'

export function ApiLoadingProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState(0)
  useEffect(() => subscribeApiLoading(setPending), [])

  const overlay =
    pending > 0 ? (
      <div
        className="api-loading-overlay"
        role="status"
        aria-live="polite"
        aria-busy="true"
        aria-label="Loading, please wait"
      >
        <div className="api-loading-card">
          <p className="api-loading-eyebrow">Please wait</p>
          <p className="api-loading-title">Working on your request…</p>
          <div className="api-loading-bars-row" aria-hidden="true">
            <div className="api-loading-bars">
              <span />
              <span />
              <span />
              <span />
            </div>
          </div>
        </div>
      </div>
    ) : null

  return (
    <>
      {children}
      {typeof document !== 'undefined' && overlay ? createPortal(overlay, document.body) : null}
    </>
  )
}
