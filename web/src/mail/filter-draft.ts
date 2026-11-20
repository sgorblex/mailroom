import { useContext } from 'react'
import { useNavigate } from '@tanstack/react-router'

import { AuthContext } from '@/auth/auth-context.ts'

import type { ActionContext } from './action-registry.ts'

/** Whether the instance runs filters, so the mail screen offers to draft one. */
export function useFiltersOffered(): boolean {
  return useContext(AuthContext)?.features.has('filters') === true
}

/** Opens the filter drafted from a message, where the instance runs filters; else `undefined`. */
export function useDraftFilter(): ActionContext['draftFilter'] {
  const offered = useFiltersOffered()
  const navigate = useNavigate()
  if (!offered) {
    return undefined
  }
  return (message) => {
    void navigate({ to: '/filters/new', search: { account: message.account_id, message: message.ref } })
  }
}
