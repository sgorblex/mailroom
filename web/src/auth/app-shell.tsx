import { useState, type KeyboardEvent, type MouseEvent, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet, useMatchRoute, useNavigate } from '@tanstack/react-router'
import { Ellipsis } from 'lucide-react'

import type { Feature } from '@/api/auth.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { navPages, type NavPage, type NavPath } from '@/auth/nav-pages.ts'
import { Menu, type MenuItem } from '@/components/ui/menu.tsx'
import { cn } from '@/lib/utils.ts'
import { LiveUpdatesProvider } from '@/mail/live-provider.tsx'

/**
 * The signed-in frame: a header with the mark, the nav (from `md` up) and the user menu; on phones
 * a tab bar fixed at the bottom instead of the nav, its height left free by `--tab-bar`. The page
 * draws under the notch (`viewport-fit=cover`), so the shell and the tab bar keep clear of the
 * left and right safe areas themselves. Mounted only while signed in, so a sign-out failure shown
 * here goes away with it.
 */
export function AppShell({
  features,
  username,
  isAdmin,
}: {
  features: ReadonlySet<Feature>
  username: string
  isAdmin: boolean
}): ReactElement {
  const { t } = useTranslation()
  const { signOut } = useAuth()
  const [signOutFailed, setSignOutFailed] = useState(false)
  // Sign out is disabled while its request is out, so a second one cannot follow it.
  const [signingOut, setSigningOut] = useState(false)
  const pages = navPages(features, isAdmin)

  function onSignOut(): void {
    if (signingOut) {
      return
    }
    setSignOutFailed(false)
    setSigningOut(true)
    signOut().catch(() => {
      setSigningOut(false)
      setSignOutFailed(true)
    })
  }
  const signOutItem: MenuItem = {
    label: t('auth.signOut'),
    onSelect: onSignOut,
    disabledReason: signingOut ? t('auth.signingOut') : null,
  }

  return (
    <div className="flex min-h-dvh flex-col pr-[env(safe-area-inset-right)] pb-(--tab-bar) pl-[env(safe-area-inset-left)]">
      <header className="flex items-center gap-3 border-b bg-card px-3 py-2 md:gap-6 md:px-4">
        <Link to="/" className="flex items-center gap-2 rounded-md font-semibold tracking-tight">
          <span aria-hidden="true" className="grid size-7 place-items-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
            M
          </span>
          Mailroom
        </Link>
        <nav aria-label={t('nav.label')} className="hidden md:block">
          <ul className="flex items-center gap-1 text-sm">
            {pages
              .filter((page) => page.place !== 'user')
              .map((page) => (
                <li key={page.to}>
                  <HeaderLink page={page} />
                </li>
              ))}
          </ul>
        </nav>
        <div className="ml-auto flex items-center">
          <UserMenu username={username} pages={pages.filter((page) => page.place === 'user')} signOut={signOutItem} />
        </div>
      </header>
      {/* Under the header, not in it: a full nav leaves the row no width for it. */}
      {signOutFailed && (
        <p role="alert" className="border-b bg-card px-3 py-1.5 text-sm text-destructive md:px-4">
          {t('auth.signOutFailed')}
        </p>
      )}
      {/* Its stream closes with the signed-in layout. */}
      {features.has('mail') ? (
        <LiveUpdatesProvider>
          <Outlet />
        </LiveUpdatesProvider>
      ) : (
        <Outlet />
      )}
      <TabBar pages={pages} signOut={signOutItem} />
    </div>
  )
}

// A rule set's page (/filters/…) still sits under Filters; only Home is matched exactly.
const activeOptions = (to: NavPath): { exact: boolean } => ({ exact: to === '/' })

function HeaderLink({ page }: { page: NavPage }): ReactElement {
  const { t } = useTranslation()
  const Icon = page.icon
  return (
    <Link
      to={page.to}
      className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5"
      inactiveProps={{ className: 'text-muted-foreground hover:bg-accent hover:text-foreground' }}
      activeProps={{ className: 'bg-accent-soft font-medium text-accent-soft-foreground', 'aria-current': 'page' }}
      activeOptions={activeOptions(page.to)}
    >
      <Icon aria-hidden="true" className="size-4" />
      {t(page.label)}
    </Link>
  )
}

/** The menu button pattern: Enter, Space, ↓ or ↑ open it; `Menu` moves, closes and gives focus back. */
function useMenuButton(): {
  anchor: HTMLElement | null
  close: () => void
  buttonProps: {
    'aria-haspopup': 'menu'
    'aria-expanded': boolean
    onClick: (event: MouseEvent<HTMLButtonElement>) => void
    onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
  }
} {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return {
    anchor,
    close: () => {
      setAnchor(null)
    },
    buttonProps: {
      'aria-haspopup': 'menu',
      'aria-expanded': anchor !== null,
      onClick: (event) => {
        setAnchor(anchor === null ? event.currentTarget : null)
      },
      onKeyDown: (event) => {
        if (anchor === null && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
          event.preventDefault()
          setAnchor(event.currentTarget)
        }
      },
    },
  }
}

/** The menu items for `pages`, then Sign out. */
function useMenuItems(pages: NavPage[], signOut: MenuItem): MenuItem[] {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return [
    ...pages.map((page) => ({
      label: t(page.label),
      onSelect: () => {
        void navigate({ to: page.to })
      },
    })),
    signOut,
  ]
}

function UserMenu({ username, pages, signOut }: { username: string; pages: NavPage[]; signOut: MenuItem }): ReactElement {
  const { t } = useTranslation()
  const items = useMenuItems(pages, signOut)
  const { anchor, close, buttonProps } = useMenuButton()
  const signedInAs = t('auth.signedInAs', { username })
  return (
    <>
      <button
        type="button"
        aria-label={t('nav.userMenu')}
        title={signedInAs}
        {...buttonProps}
        className="grid size-8 place-items-center rounded-full bg-accent-soft text-sm font-semibold text-accent-soft-foreground outline-none hover:ring-2 hover:ring-ring/30 focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span aria-hidden="true">{Array.from(username)[0]?.toUpperCase()}</span>
      </button>
      {anchor !== null && (
        <Menu
          label={signedInAs}
          heading={signedInAs}
          anchor={anchor}
          items={items}
          onClose={close}
        />
      )}
    </>
  )
}

const TAB = 'flex flex-1 flex-col items-center gap-0.5 py-1.5 text-[0.6875rem] font-medium'

function TabIcon({ icon: Icon, active }: { icon: NavPage['icon']; active: boolean }): ReactElement {
  return (
    <span
      className={cn('rounded-full px-4 py-1', active ? 'bg-accent-soft text-accent-soft-foreground' : 'text-muted-foreground')}
    >
      <Icon aria-hidden="true" className="size-5" />
    </span>
  )
}

/**
 * Phones only (below `md`): Home, Mail and Filters as the instance has them, then More with the
 * other pages, Profile and Sign out. Over the home indicator's and the side safe areas.
 */
function TabBar({ pages, signOut }: { pages: NavPage[]; signOut: MenuItem }): ReactElement {
  const { t } = useTranslation()
  const matchRoute = useMatchRoute()
  const { anchor, close, buttonProps } = useMenuButton()
  const tabs = pages.filter((page) => page.place === 'tab')
  const more = pages.filter((page) => page.place !== 'tab')
  const items = useMenuItems(more, signOut)
  // The links' own matching (`activeOptions`): a page's subpaths count, and only Home is exact.
  const onMorePage = more.some((page) => matchRoute({ to: page.to, fuzzy: page.to !== '/' }) !== false)
  return (
    <nav
      aria-label={t('nav.label')}
      className="fixed inset-x-0 bottom-0 z-40 border-t bg-card pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] md:hidden"
    >
      <ul className="flex">
        {tabs.map((page) => (
          <li key={page.to} className="flex flex-1">
            <Link
              to={page.to}
              className={TAB}
              activeProps={{ className: 'text-foreground', 'aria-current': 'page' }}
              inactiveProps={{ className: 'text-muted-foreground' }}
              activeOptions={activeOptions(page.to)}
            >
              {({ isActive }) => (
                <>
                  <TabIcon icon={page.icon} active={isActive} />
                  {t(page.label)}
                </>
              )}
            </Link>
          </li>
        ))}
        <li className="flex flex-1">
          <button type="button" {...buttonProps} className={cn(TAB, onMorePage ? 'text-foreground' : 'text-muted-foreground')}>
            <TabIcon icon={Ellipsis} active={onMorePage} />
            {t('nav.more')}
          </button>
          {anchor !== null && (
            <Menu
              label={t('nav.more')}
              anchor={anchor}
              items={items}
              onClose={close}
            />
          )}
        </li>
      </ul>
    </nav>
  )
}
