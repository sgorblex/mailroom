import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Outlet } from '@tanstack/react-router'

import { useAuth } from '@/auth/auth-context.ts'
import { LoginScreen } from '@/auth/login-screen.tsx'
import { Button } from '@/components/ui/button.tsx'

/** Every page sits behind sign-in. */
export function RootLayout(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()

  if (auth.status === 'checking') {
    return (
      <p role="status" className="p-6 text-muted-foreground">
        {t('auth.checking')}
      </p>
    )
  }
  if (auth.status === 'unreachable') {
    return (
      <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-3 p-6">
        <p role="alert">{t('auth.unreachable')}</p>
        <Button className="self-start" variant="outline" size="sm" onClick={auth.recheck}>
          {t('auth.retry')}
        </Button>
      </main>
    )
  }
  if (auth.status === 'signed-out') {
    return <LoginScreen />
  }
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-end gap-3 p-3">
        <p className="text-sm text-muted-foreground">
          {t('auth.signedInAs', { username: auth.user.username })}
        </p>
        <SignOutButton />
      </header>
      <Outlet />
    </div>
  )
}

/** Its own component, so a failure shown here goes away with the signed-in layout. */
function SignOutButton(): ReactElement {
  const { t } = useTranslation()
  const { signOut } = useAuth()
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  return (
    <>
      {failed && (
        <p role="alert" className="text-sm text-destructive">
          {t('auth.signOutFailed')}
        </p>
      )}
      <Button
        variant="ghost"
        size="sm"
        disabled={pending}
        onClick={() => {
          setPending(true)
          setFailed(false)
          signOut().catch(() => {
            setFailed(true)
            setPending(false)
          })
        }}
      >
        {t('auth.signOut')}
      </Button>
    </>
  )
}
