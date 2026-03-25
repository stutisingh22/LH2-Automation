import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import {
  applyWorkspaceToSearchParams,
  workspaceFromSearchParams,
  workspaceKeysEqual,
  workspaceParamsFromSelection,
  selectionWorkspaceEqual,
} from '../lib/workspaceUrl'

const KEY = 'lh2_selection_v2'

export type CompanySource = 'independent' | 'media'

export type SelectionState = {
  source: CompanySource
  companyId: number | null
  contactId: number | null
  advisorCompanyId: number | null
  sessionId: number | null
}

const defaultSel: SelectionState = {
  source: 'independent',
  companyId: null,
  contactId: null,
  advisorCompanyId: null,
  sessionId: null,
}

function loadSession(): SelectionState {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return { ...defaultSel }
    const j = JSON.parse(raw) as Partial<SelectionState>
    return {
      source: j.source === 'media' ? 'media' : 'independent',
      companyId: typeof j.companyId === 'number' ? j.companyId : null,
      contactId: typeof j.contactId === 'number' ? j.contactId : null,
      advisorCompanyId: typeof j.advisorCompanyId === 'number' ? j.advisorCompanyId : null,
      sessionId: typeof j.sessionId === 'number' ? j.sessionId : null,
    }
  } catch {
    return { ...defaultSel }
  }
}

function readInitial(): SelectionState {
  try {
    const sp = new URLSearchParams(window.location.search)
    const fromUrl = workspaceFromSearchParams(sp)
    if (fromUrl) return fromUrl
  } catch {
    /* ignore */
  }
  return loadSession()
}

type Ctx = {
  selection: SelectionState
  setSelection: (p: Partial<SelectionState>) => void
  clearWorkspace: () => void
}

const SelectionContext = createContext<Ctx | null>(null)

export function SelectionProvider({ children }: { children: ReactNode }) {
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [selection, setSel] = useState<SelectionState>(readInitial)

  useLayoutEffect(() => {
    const w = workspaceFromSearchParams(new URLSearchParams(searchParams.toString()))
    if (!w) return
    setSel((prev) => (selectionWorkspaceEqual(prev, w) ? prev : w))
  }, [searchParams])

  useEffect(() => {
    const cur = new URLSearchParams(location.search)
    const expected = workspaceParamsFromSelection(selection)
    if (workspaceKeysEqual(cur, expected)) return
    const next = new URLSearchParams(location.search)
    applyWorkspaceToSearchParams(next, selection)
    setSearchParams(next, { replace: true })
  }, [selection, location.search, setSearchParams])

  useEffect(() => {
    sessionStorage.setItem(KEY, JSON.stringify(selection))
  }, [selection])

  const setSelection = useCallback((p: Partial<SelectionState>) => {
    setSel((prev) => ({ ...prev, ...p }))
  }, [])

  const clearWorkspace = useCallback(() => {
    setSel((prev) => ({
      ...prev,
      companyId: null,
      contactId: null,
      advisorCompanyId: null,
      sessionId: null,
    }))
  }, [])

  const v = useMemo(
    () => ({ selection, setSelection, clearWorkspace }),
    [selection, setSelection, clearWorkspace],
  )

  return <SelectionContext.Provider value={v}>{children}</SelectionContext.Provider>
}

export function useSelection() {
  const c = useContext(SelectionContext)
  if (!c) throw new Error('useSelection outside provider')
  return c
}
