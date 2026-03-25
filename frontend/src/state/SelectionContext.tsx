import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

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

function load(): SelectionState {
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

type Ctx = {
  selection: SelectionState
  setSelection: (p: Partial<SelectionState>) => void
  clearWorkspace: () => void
}

const SelectionContext = createContext<Ctx | null>(null)

export function SelectionProvider({ children }: { children: ReactNode }) {
  const [selection, setSel] = useState<SelectionState>(defaultSel)

  useEffect(() => {
    setSel(load())
  }, [])

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
