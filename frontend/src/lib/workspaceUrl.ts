import type { SelectionState } from '../state/SelectionContext'

export const WORKSPACE_QUERY_KEYS = [
  'sel_source',
  'sel_company',
  'sel_advisor',
  'sel_contact',
  'sel_session',
] as const

export type WorkspaceQueryKey = (typeof WORKSPACE_QUERY_KEYS)[number]

function parseNum(v: string | null): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export function workspaceParamsPresent(sp: URLSearchParams): boolean {
  return WORKSPACE_QUERY_KEYS.some((k) => sp.has(k))
}

export function workspaceFromSearchParams(sp: URLSearchParams): SelectionState | null {
  if (!workspaceParamsPresent(sp)) return null
  return {
    source: sp.get('sel_source') === 'media' ? 'media' : 'independent',
    companyId: parseNum(sp.get('sel_company')),
    advisorCompanyId: parseNum(sp.get('sel_advisor')),
    contactId: parseNum(sp.get('sel_contact')),
    sessionId: parseNum(sp.get('sel_session')),
  }
}

export function applyWorkspaceToSearchParams(sp: URLSearchParams, sel: SelectionState) {
  sp.set('sel_source', sel.source)
  if (sel.companyId != null) sp.set('sel_company', String(sel.companyId))
  else sp.delete('sel_company')
  if (sel.advisorCompanyId != null) sp.set('sel_advisor', String(sel.advisorCompanyId))
  else sp.delete('sel_advisor')
  if (sel.contactId != null) sp.set('sel_contact', String(sel.contactId))
  else sp.delete('sel_contact')
  if (sel.sessionId != null) sp.set('sel_session', String(sel.sessionId))
  else sp.delete('sel_session')
}

export function stripWorkspaceFromSearchParams(sp: URLSearchParams) {
  for (const k of WORKSPACE_QUERY_KEYS) sp.delete(k)
}

export function workspaceParamsFromSelection(sel: SelectionState): URLSearchParams {
  const sp = new URLSearchParams()
  applyWorkspaceToSearchParams(sp, sel)
  return sp
}

export function workspaceKeysEqual(a: URLSearchParams, b: URLSearchParams): boolean {
  for (const k of WORKSPACE_QUERY_KEYS) {
    if ((a.get(k) ?? '') !== (b.get(k) ?? '')) return false
  }
  return true
}

export function mergeSelectionIntoSearchString(search: string, sel: SelectionState): URLSearchParams {
  const next = new URLSearchParams(search)
  applyWorkspaceToSearchParams(next, sel)
  return next
}

export function toWorkspaceSearchString(sel: SelectionState): string {
  return workspaceParamsFromSelection(sel).toString()
}

export function selectionWorkspaceEqual(a: SelectionState, b: SelectionState): boolean {
  return (
    a.source === b.source &&
    a.companyId === b.companyId &&
    a.contactId === b.contactId &&
    a.advisorCompanyId === b.advisorCompanyId &&
    a.sessionId === b.sessionId
  )
}
