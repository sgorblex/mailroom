import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiGetJson } from '@/api/http.ts'

import { AuthProvider } from './auth-provider.tsx'
import { RootLayout } from './root-layout.tsx'

function renderApp(): void {
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
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  )
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true }

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
): ReturnType<typeof vi.fn> {
  let signedIn = initiallySignedIn
  let user: { username: string; is_admin: boolean; time_zone: string | null; mark_read_subfolders: boolean } = { ...ALICE, time_zone: timeZone }
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
    expect(screen.getByText('Signed in as alice')).toBeInTheDocument()
  })

  it('shows the page to a signed-in browser and signs out', async () => {
    stubApi(true)
    renderApp()

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveAttribute('href', '/profile')
    expect(screen.getByRole('link', { name: 'Mail' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Filters' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
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
    expect(screen.getByRole('link', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Mail' }) !== null).toBe(mail)
    expect(screen.queryByRole('link', { name: 'Filters' }) !== null).toBe(filters)
    expect(screen.queryByRole('link', { name: 'Activity' }) !== null).toBe(activity)
  })

  it('keeps the features it learned while signed out once signed in', async () => {
    stubApi(false, ['filters'])
    renderApp()

    await userEvent.type(await screen.findByLabelText('Username'), 'alice')
    await userEvent.type(screen.getByLabelText('Password'), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('link', { name: 'Filters' })).toBeInTheDocument()
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
