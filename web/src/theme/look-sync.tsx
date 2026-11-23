import { useEffect } from 'react'

import { useAuth } from '@/auth/auth-context.ts'
import { setLook } from '@/theme/look.ts'

/** Shows the signed-in user's saved look, which wins over this browser's copy, whenever it changes. */
export function LookSync(): null {
  const auth = useAuth()
  const appearance = auth.status === 'signed-in' ? auth.user.appearance : null
  const theme = appearance?.theme
  const accent = appearance?.accent
  const textSize = appearance?.text_size
  useEffect(() => {
    if (theme !== undefined && accent !== undefined && textSize !== undefined) {
      setLook({ theme, accent, text_size: textSize })
    }
  }, [theme, accent, textSize])
  return null
}
