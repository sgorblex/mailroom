import { useState, type ReactElement, type ReactNode } from 'react'
import { vi } from 'vitest'

import { changeInboxSubfolders, type Feature, type User } from '@/api/auth.ts'
import { AuthContext } from '@/auth/auth-context.ts'

/** Signed in as `user`, whose All inboxes choice is saved as the app saves it. */
export function SignedIn({ user, features, children }: { user: User; features: ReadonlySet<Feature>; children: ReactNode }): ReactElement {
  const [shown, setShown] = useState(user)
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  return (
    <AuthContext
      value={{
        status: 'signed-in',
        user: shown,
        features,
        signIn: reject,
        signOut: reject,
        changeTimeZone: reject,
        changeMarkReadSubfolders: reject,
        changeIndexMaxAge: reject,
        changeConversations: reject,
        changeInboxSubfolders: async (included) => {
          setShown(await changeInboxSubfolders(included))
        },
        changeUndoSend: reject,
        changeAppearance: reject,
        recheck: vi.fn(),
      }}
    >
      {children}
    </AuthContext>
  )
}
