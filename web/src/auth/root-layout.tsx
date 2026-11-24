import { type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { AppShell } from '@/auth/app-shell.tsx'
import { useAuth } from '@/auth/auth-context.ts'
import { LoginScreen } from '@/auth/login-screen.tsx'
import { Button } from '@/components/ui/button.tsx'
import { DeployBanner } from '@/components/deploy-banner.tsx'

/** Every page sits behind sign-in, under the deploy banner; the nav shows only the instance's
 * features. */
export function RootLayout(): ReactElement {
  return (
    <>
      <DeployBanner />
      <SignedInLayout />
    </>
  )
}

function SignedInLayout(): ReactElement {
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
  return <AppShell features={auth.features} username={auth.user.username} isAdmin={auth.user.is_admin} />
}
