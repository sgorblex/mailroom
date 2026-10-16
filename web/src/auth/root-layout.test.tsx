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

import { AuthProvider } from './auth-provider.tsx'
import { RootLayout } from './root-layout.tsx'

function renderApp(): void {
  const rootRoute = createRootRoute({ component: RootLayout })
  const homeRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    component: () => <p>Home page</p>,
  })
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
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

/** A fake API: `/me` answers from `signedIn`; login accepts alice / `hunter2hunter2`. */
function stubApi(initiallySignedIn: boolean): void {
  let signedIn = initiallySignedIn
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      if (path === '/api/v1/auth/me') {
        return Promise.resolve(json(200, { user: signedIn ? ALICE : null }))
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
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
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
