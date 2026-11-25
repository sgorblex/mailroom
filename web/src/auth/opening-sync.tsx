import { useEffect } from 'react'

import { useAuth } from '@/auth/auth-context.ts'
import { OPENING_PREF } from '@/auth/opening.ts'
import { writeDevicePref } from '@/lib/device-prefs.ts'

/** Keeps this browser's copy of the opening (`Opening`) in step with the signed-in user's `/me`. */
export function OpeningSync(): null {
  const auth = useAuth()
  const home = auth.status === 'signed-in' ? auth.user.appearance.home : null
  const mail = auth.features.has('mail')
  useEffect(() => {
    if (home !== null) {
      writeDevicePref(OPENING_PREF, { home, mail })
    }
  }, [home, mail])
  return null
}
