import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { apiFetch } from '../api/client'

export type UiConfig = {
  contact_statuses: string[]
  contact_status_labels: Record<string, string>
  contact_status_labels_strip: Record<string, string>
  action_types: string[]
  action_channels: string[]
  action_statuses: string[]
  action_status_colors: Record<string, string>
  products: { key: string; label: string }[]
}

const MetaContext = createContext<UiConfig | null>(null)

export function MetaProvider({ children }: { children: ReactNode }) {
  const [cfg, setCfg] = useState<UiConfig | null>(null)

  useEffect(() => {
    apiFetch<UiConfig>('/api/meta/config', { silent: true })
      .then(setCfg)
      .catch(() =>
        setCfg({
          contact_statuses: [],
          contact_status_labels: {},
          contact_status_labels_strip: {},
          action_types: [],
          action_channels: [],
          action_statuses: [],
          action_status_colors: {},
          products: [],
        }),
      )
  }, [])

  const v = useMemo(() => cfg, [cfg])
  return <MetaContext.Provider value={v}>{children}</MetaContext.Provider>
}

export function useMeta() {
  return useContext(MetaContext)
}
