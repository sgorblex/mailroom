import { render, screen } from '@testing-library/react'
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

const ALICE = { username: 'alice', is_admin: false }

/**
 * A fake API: `/me` answers from `signedIn`, naming `features` unless left out; login accepts
 * alice / `hunter2hunter2`.
 */
function stubApi(initiallySignedIn: boolean, features?: string[]): void {
  let signedIn = initiallySignedIn
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      if (path === '/api/v1/auth/me') {
        return Promise.resolve(json(200, { user: signedIn ? ALICE : null, features }))
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
        return Promise.resolve(json(200, ALICE))
      }
      if (path === '/api/v1/auth/logout') {
        signedIn = false
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      if (path === '/api/v1/users') {
        return Promise.resolve(json(401, { detail: 'Not authenticated' }))
      }
      return Promise.resolve(json(404, { detail: 'Not Found' }))
    }),
  )
}

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
