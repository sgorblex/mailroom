import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiGetJson } from '@/api/http.ts'
import { createQueryClient } from '@/lib/query-client.ts'
import { stubEventSource } from '@/test/fake-event-source.ts'

import { AuthProvider } from './auth-provider.tsx'
import { RootLayout } from './root-layout.tsx'

function renderApp(path = '/'): void {
  const rootRoute = createRootRoute({ component: RootLayout })
  const homeRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => (
      <>
        <p>Home page</p>
        <button type="button" onClick={() => void apiGetJson('/api/v1/users').catch(() => null)}>
          Load users
        </button>
      </>
    ),
  })
  const pages = Object.entries({ '/profile': 'Profile page', '/accounts': 'Accounts page', '/filters/$ruleSetId': 'Rule set page' }).map(
    ([at, text]) => createRoute({ getParentRoute: () => rootRoute, path: at, component: () => <p>{text}</p> }),
  )
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, ...pages]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** The header's nav and the phone tab bar: both are rendered, CSS shows one (`md:block`, `md:hidden`). */
function navs(): { header: HTMLElement; tabs: HTMLElement } {
  const [header, tabs] = screen.getAllByRole('navigation', { name: 'Main' })
  if (header === undefined || tabs === undefined) throw new Error('Two navs expected')
  return { header, tabs }
}

const linkNames = (nav: HTMLElement): string[] => within(nav).queryAllByRole('link').map((link) => link.textContent)

async function openUserMenu(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole('button', { name: 'User menu' }))
  return screen.getByRole('menu', { name: 'Signed in as alice' })
}

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

/**
 * A fake API: `/me` answers from `signedIn`, naming `features` unless left out; login accepts
 * alice / `hunter2hunter2`, whose time zone is `timeZone` until one is saved; `refuseZone` refuses
 * every zone saved.
 */
function stubApi(
  initiallySignedIn: boolean,
  features?: string[],
  timeZone: string | null = null,
  refuseZone = false,
  { admin = false, failSignOut = false } = {},
): ReturnType<typeof vi.fn> {
  let signedIn = initiallySignedIn
  let user: { username: string; is_admin: boolean; time_zone: string | null; mark_read_subfolders: boolean; index_max_age_days: number | null; conversations: boolean } = { ...ALICE, is_admin: admin, time_zone: timeZone }
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
      if (path === '/api/v1/auth/me') {
        return Promise.resolve(json(200, { user: signedIn ? user : null, features }))
      }
      if (path === '/api/v1/auth/me/time-zone') {
        if (refuseZone) {
          return Promise.resolve(json(422, { detail: [{ loc: ['body', 'time_zone'], msg: 'Unknown time zone' }] }))
        }
        user = { ...user, time_zone: (JSON.parse(init?.body as string) as { time_zone: string }).time_zone }
        return Promise.resolve(json(200, user))
      }
      if (path === '/api/v1/auth/login') {
        const { username, password } = JSON.parse(init?.body as string) as {
          username: string
          password: string
        }
        if (username !== 'alice' || password !== 'hunter2hunter2') {
          return Promise.resolve(json(401, { detail: 'Wrong username or password' }))
        }
        signedIn = true
        return Promise.resolve(json(200, user))
      }
      if (path === '/api/v1/auth/logout') {
        if (failSignOut) {
          return Promise.resolve(json(500, { detail: 'Internal Server Error' }))
        }
        signedIn = false
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      if (path === '/api/v1/users') {
        return Promise.resolve(json(401, { detail: 'Not authenticated' }))
      }
      return Promise.resolve(json(404, { detail: 'Not Found' }))
    })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const zoneSaves = (fetchMock: ReturnType<typeof vi.fn>): unknown[] =>
  fetchMock.mock.calls
    .filter(([path]) => path === '/api/v1/auth/me/time-zone')
    .map(([, init]): unknown => JSON.parse((init as RequestInit).body as string))

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('RootLayout', () => {
  it('asks who you are, refuses a wrong password, and shows the page after signing in', async () => {
    stubApi(false)
    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'alice')
    const password = screen.getByLabelText('Password')
    expect(screen.queryByText('Home page')).not.toBeInTheDocument()

    await userEvent.type(password, 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong username or password.')

    await userEvent.clear(password)
    await userEvent.type(password, 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'User menu' })).toHaveAttribute('title', 'Signed in as alice')
  })

  it('shows the page to a signed-in browser and signs out', async () => {
    stubApi(true)
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(linkNames(navs().header)).toEqual(['Home', 'Mail', 'Accounts', 'Filters', 'Activity'])
    const menu = await openUserMenu()
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Profile', 'API status', 'Sign out'])
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
  })

  it("opens the user's live updates stream while signed in to the mail client, and closes it on signing out", async () => {
    const sources = stubEventSource()
    stubApi(true, ['mail'])
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(sources.instances.map((source) => [source.url, source.withCredentials])).toEqual([['/api/v1/mail/events', true]])
    await userEvent.click(within(await openUserMenu()).getByRole('menuitem', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(sources.current()).toBeUndefined()
  })

  it('opens no stream on an instance without the mail client', async () => {
    const sources = stubEventSource()
    stubApi(true, ['filters'])
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(sources.instances).toHaveLength(0)
  })

  it.each([
    { features: [], mail: false, filters: false, activity: false },
    { features: ['mcp'], mail: false, filters: false, activity: true },
    { features: ['mail'], mail: true, filters: false, activity: true },
    { features: ['filters'], mail: false, filters: true, activity: true },
  ])('links only to the features the instance runs ($features)', async ({ features, mail, filters, activity }) => {
    stubApi(true, features)
    renderApp()

    expect(await screen.findByRole('link', { name: 'Accounts' })).toBeInTheDocument()
    const { header, tabs } = navs()
    expect(linkNames(header).includes('Mail')).toBe(mail)
    expect(linkNames(header).includes('Filters')).toBe(filters)
    expect(linkNames(header).includes('Activity')).toBe(activity)
    expect(linkNames(header)).not.toContain('Profile')
    // On phones: a tab for each of Home, Mail and Filters the instance has, then More.
    expect(linkNames(tabs)).toEqual(['Home', ...(mail ? ['Mail'] : []), ...(filters ? ['Filters'] : [])])
    expect(within(tabs).getByRole('button', { name: 'More' })).toHaveAttribute('aria-haspopup', 'menu')
  })

  it('keeps the features it learned while signed out once signed in', async () => {
    stubApi(false, ['filters'])
    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'alice')
    await userEvent.type(screen.getByLabelText('Password'), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findAllByRole('link', { name: 'Filters' })).toHaveLength(2)
    expect(screen.queryByRole('link', { name: 'Mail' })).not.toBeInTheDocument()
  })

  it('signs out and says why when a request finds the session ended', async () => {
    stubApi(true)
    renderApp()

    await userEvent.click(await screen.findByRole('button', { name: 'Load users' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Your session has ended.')
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.queryByText('Home page')).not.toBeInTheDocument()
  })

  it('saves the browser\'s time zone once for a user who has none', async () => {
    const fetchMock = stubApi(false)
    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'alice')
    await userEvent.type(screen.getByLabelText('Password'), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Home page')).toBeInTheDocument()
    await waitFor(() => {
      expect(zoneSaves(fetchMock)).toEqual([{ time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone }])
    })
  })

  it('shows the app and tries no more when the browser\'s time zone is refused', async () => {
    const fetchMock = stubApi(true, undefined, null, true)
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    await waitFor(() => {
      expect(zoneSaves(fetchMock)).toHaveLength(1)
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.getByText('Home page')).toBeInTheDocument()
    expect(zoneSaves(fetchMock)).toHaveLength(1)
  })

  it('leaves a saved time zone alone', async () => {
    const fetchMock = stubApi(true, undefined, 'Asia/Tokyo')
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(zoneSaves(fetchMock)).toEqual([])
  })

  it('marks the page you are on, and a rule set as under Filters', async () => {
    stubApi(true)
    renderApp('/filters/7')

    expect(await screen.findByText('Rule set page')).toBeInTheDocument()
    const { header, tabs } = navs()
    const current = within(header).getByRole('link', { name: 'Filters' })
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(current).toHaveClass('bg-accent-soft', 'text-accent-soft-foreground')
    expect(within(header).getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current')
    expect(within(tabs).getByRole('link', { name: 'Filters' })).toHaveAttribute('aria-current', 'page')
  })

  it('opens the user menu from the keyboard, goes to the Profile, and gives focus back on Escape', async () => {
    stubApi(true)
    renderApp()

    const button = await screen.findByRole('button', { name: 'User menu' })
    expect(button).toHaveTextContent('A')
    button.focus()
    await userEvent.keyboard('{ArrowDown}')
    const menu = screen.getByRole('menu', { name: 'Signed in as alice' })
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(within(menu).getByRole('menuitem', { name: 'Profile' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button).toHaveFocus()

    await userEvent.keyboard('{Enter}')
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByText('Profile page')).toBeInTheDocument()
  })

  it('says so when signing out fails', async () => {
    stubApi(true, undefined, null, false, { failSignOut: true })
    renderApp()

    await userEvent.click(within(await openUserMenu()).getByRole('menuitem', { name: 'Sign out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign out.')
    expect(screen.getByText('Home page')).toBeInTheDocument()
  })

  it('disables Sign out while its request is out, and gives it back when it fails', async () => {
    const api = stubApi(true, undefined, null, false, { failSignOut: true })
    let answer = (): void => undefined
    const answerOf = api as unknown as (path: string, init?: RequestInit) => Promise<Response>
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) =>
      path === '/api/v1/auth/logout'
        ? new Promise<Response>((resolve) => {
            answer = () => {
              resolve(answerOf(path, init))
            }
          })
        : answerOf(path, init),
    )
    renderApp()

    await userEvent.click(within(await openUserMenu()).getByRole('menuitem', { name: 'Sign out' }))
    const pending = within(await openUserMenu()).getByRole('menuitem', { name: /^Sign out/ })
    expect(pending).toHaveAttribute('aria-disabled', 'true')
    expect(pending).toHaveTextContent('Signing out…')
    await userEvent.click(pending)
    answer()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign out.')
    expect(api.mock.calls.filter(([path]) => path === '/api/v1/auth/logout')).toHaveLength(1)
    // The menu stays open on a disabled item; its Sign out is back.
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).not.toHaveAttribute('aria-disabled')
  })

  it("lists the phone's other pages under More, Users to an admin, and marks More on one of them", async () => {
    stubApi(true, ['mail'], null, false, { admin: true })
    renderApp('/accounts')

    expect(await screen.findByText('Accounts page')).toBeInTheDocument()
    const more = within(navs().tabs).getByRole('button', { name: 'More' })
    expect(more).toHaveClass('text-foreground')
    await userEvent.click(more)
    const menu = screen.getByRole('menu', { name: 'More' })
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      'Accounts',
      'Activity',
      'Users',
      'Profile',
      'API status',
      'Sign out',
    ])
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Profile' }))
    expect(await screen.findByText('Profile page')).toBeInTheDocument()
    expect(more).toHaveClass('text-foreground')
    await userEvent.click(within(navs().tabs).getByRole('link', { name: 'Home' }))
    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(more).toHaveClass('text-muted-foreground')
  })

  it('offers to try again when the API cannot be reached', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Mailroom could not reach its API.',
    )
    stubApi(true)
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Home page')).toBeInTheDocument()
  })
})
