import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { useAuth } from './auth-context.ts'

/** What one user read is dropped from the cache once they are no longer signed in. */
export function ClearOnSignOut(): null {
  const { status } = useAuth()
  const queryClient = useQueryClient()
  useEffect(() => {
    if (status !== 'signed-in') {
      queryClient.clear()
    }
  }, [status, queryClient])
  return null
}
