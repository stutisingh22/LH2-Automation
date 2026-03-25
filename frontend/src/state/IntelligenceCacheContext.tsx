import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from 'react'

/** Minimal shapes for tab-switch restore (matches API workspace + sections). */
export type IntelWorkspaceSnapshot = {
  profile: Record<string, unknown>
  research_full: string
  research_display_html: string
  research_sections?: IntelSectionSnapshot[]
  recommended_offering?: Record<string, unknown> | null
  platform_presence: Record<string, unknown>
  social_from_search: Record<string, unknown>
  platform_presence_analytics: Record<string, unknown>
  notion_notes: unknown[]
  notion_page_id?: string | null
  advisor_canonical: boolean
}

export type IntelSectionSnapshot = { title: string; body_html: string }

export type IntelligenceTabSnapshot = {
  ws: IntelWorkspaceSnapshot | null
  sections: IntelSectionSnapshot[]
  overrides: Record<number, string>
  openSectionIds: number[]
}

type Ctx = {
  getSnapshot: (key: string) => IntelligenceTabSnapshot | undefined
  putSnapshot: (key: string, s: IntelligenceTabSnapshot) => void
  clearByPrefix: (prefix: string) => void
}

const IntelligenceCacheContext = createContext<Ctx | null>(null)
const CACHE_KEY = 'lh2_intelligence_cache_v1'

function loadInitialMap(): Map<string, IntelligenceTabSnapshot> {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY)
    if (!raw) return new Map<string, IntelligenceTabSnapshot>()
    const obj = JSON.parse(raw) as Record<string, IntelligenceTabSnapshot>
    return new Map<string, IntelligenceTabSnapshot>(Object.entries(obj))
  } catch {
    return new Map<string, IntelligenceTabSnapshot>()
  }
}

function persistMap(map: Map<string, IntelligenceTabSnapshot>) {
  try {
    const obj = Object.fromEntries(map.entries())
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(obj))
  } catch {
    /* ignore storage quota / privacy mode issues */
  }
}

export function IntelligenceCacheProvider({ children }: { children: ReactNode }) {
  const mapRef = useRef(loadInitialMap())
  const getSnapshot = useCallback((key: string) => mapRef.current.get(key), [])
  const putSnapshot = useCallback((key: string, s: IntelligenceTabSnapshot) => {
    mapRef.current.set(key, s)
    persistMap(mapRef.current)
  }, [])
  const clearByPrefix = useCallback((prefix: string) => {
    if (!prefix) return
    const map = mapRef.current
    let changed = false
    for (const key of map.keys()) {
      if (key.startsWith(prefix)) {
        map.delete(key)
        changed = true
      }
    }
    if (changed) persistMap(map)
  }, [])
  const v = useMemo(
    () => ({ getSnapshot, putSnapshot, clearByPrefix }),
    [getSnapshot, putSnapshot, clearByPrefix],
  )
  return (
    <IntelligenceCacheContext.Provider value={v}>{children}</IntelligenceCacheContext.Provider>
  )
}

export function useIntelligenceCache(): Ctx | null {
  return useContext(IntelligenceCacheContext)
}
