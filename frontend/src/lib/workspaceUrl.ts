import type { SelectionState } from '../state/SelectionContext'

export const WORKSPACE_QUERY_KEYS = [
  'source',
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
  const sourceParam = sp.get('sel_source') ?? sp.get('source')
  return {
    source: sourceParam === 'media' ? 'media' : 'independent',
    companyId: parseNum(sp.get('sel_company')),
    advisorCompanyId: parseNum(sp.get('sel_advisor')),
    contactId: parseNum(sp.get('sel_contact')),
    sessionId: parseNum(sp.get('sel_session')),
  }
}

function hasScopedSelection(sel: SelectionState): boolean {
  return sel.companyId != null || sel.advisorCompanyId != null || sel.contactId != null || sel.sessionId != null
}

export function applyWorkspaceToSearchParams(sp: URLSearchParams, sel: SelectionState) {
  if (hasScopedSelection(sel)) {
    // Selected workspace: keep source in scoped key only.
    sp.set('sel_source', sel.source)
    sp.delete('source')
    if (sel.companyId != null) sp.set('sel_company', String(sel.companyId))
    else sp.delete('sel_company')
    if (sel.advisorCompanyId != null) sp.set('sel_advisor', String(sel.advisorCompanyId))
    else sp.delete('sel_advisor')
    if (sel.contactId != null) sp.set('sel_contact', String(sel.contactId))
    else sp.delete('sel_contact')
    if (sel.sessionId != null) sp.set('sel_session', String(sel.sessionId))
    else sp.delete('sel_session')
    return
  }

  // No selected entity: keep plain source and clear scoped selection keys.
  sp.set('source', sel.source)
  sp.delete('sel_source')
  sp.delete('sel_company')
  sp.delete('sel_advisor')
  sp.delete('sel_contact')
  sp.delete('sel_session')
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
