import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet } from '@tanstack/react-router'

import { useAuth } from '@/auth/auth-context.ts'
import { LoginScreen } from '@/auth/login-screen.tsx'
import { Button } from '@/components/ui/button.tsx'

/** Every page sits behind sign-in; the nav shows only the instance's features. */
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
      <header className="flex flex-wrap items-center gap-3 p-3">
        <nav aria-label={t('nav.label')} className="mr-auto flex gap-3 text-sm">
          <NavLink to="/">{t('nav.home')}</NavLink>
          {auth.features.has('mail') && <NavLink to="/mail">{t('nav.mail')}</NavLink>}
          <NavLink to="/accounts">{t('nav.accounts')}</NavLink>
          {auth.features.has('filters') && <NavLink to="/filters">{t('nav.filters')}</NavLink>}
          {auth.user.is_admin && <NavLink to="/users">{t('nav.users')}</NavLink>}
          <NavLink to="/profile">{t('nav.profile')}</NavLink>
        </nav>
        <p className="text-sm text-muted-foreground">
          {t('auth.signedInAs', { username: auth.user.username })}
        </p>
        <SignOutButton />
      </header>
      <Outlet />
    </div>
  )
}

function NavLink({
  to,
  children,
}: {
  to: '/' | '/mail' | '/accounts' | '/filters' | '/users' | '/profile'
  children: string
}): ReactElement {
  return (
    <Link
      to={to}
      className="hover:text-foreground"
      inactiveProps={{ className: 'text-muted-foreground' }}
      activeProps={{ className: 'font-medium text-foreground', 'aria-current': 'page' }}
      // A rule set's page (/filters/…) still sits under Filters.
      activeOptions={{ exact: to === '/' }}
    >
      {children}
    </Link>
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
