import { screen } from '@testing-library/react'
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Feature } from '@/api/auth.ts'
import { routeTree } from '@/router.tsx'
import { renderSignedIn } from '@/test/signed-in.tsx'

const loaded = vi.hoisted(() => ({ mail: vi.fn(), filters: vi.fn() }))

function mailScreenModule(): { MailScreen: () => ReactElement } {
  loaded.mail()
  return { MailScreen: () => <p>Mail screen</p> }
}

vi.mock('@/mail/mail-screen.tsx', () => mailScreenModule())
vi.mock('@/filters/rule-set-screen.tsx', () => {
  loaded.filters()
  return { RuleSetRoute: () => <p>Rule set screen</p> }
})

const ALICE = { username: 'alice', is_admin: false }

function stubFetch(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(() => Promise.resolve(new Response('{"detail":"Not Found"}', { status: 404 })))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderApp(path: string, features: Feature[]): void {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  renderSignedIn(<RouterProvider router={router} />, ALICE, features)
}

afterEach(() => {
  vi.unstubAllGlobals()
  loaded.mail.mockClear()
  loaded.filters.mockClear()
})

describe('featureRoute', () => {
  it.each([
    { path: '/mail', module: 'mail' as const },
    { path: '/filters/1', module: 'filters' as const },
  ])('says $path is not available, never loading its screen', async ({ path, module }) => {
    stubFetch()
    renderApp(path, [])
    expect(
      await screen.findByRole('heading', { name: 'Not available on this instance' }),
    ).toBeInTheDocument()
    expect(loaded[module]).not.toHaveBeenCalled()
  })

  it('shows the screen when its feature is on', async () => {
    stubFetch()
    renderApp('/mail', ['mail'])
    expect(await screen.findByText('Mail screen')).toBeInTheDocument()
    expect(loaded.mail).toHaveBeenCalled()
  })

  it('does not ask for a consent request without mcp', async () => {
    const fetchMock = stubFetch()
    renderApp('/connect?request=req-1', ['mail', 'filters'])
    expect(
      await screen.findByRole('heading', { name: 'Not available on this instance' }),
    ).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('asks for the consent request with mcp', async () => {
    const fetchMock = stubFetch()
    renderApp('/connect?request=req-1', ['mcp'])
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/v1/oauth/requests/req-1', expect.anything())
    })
  })
})

describe('featureRoute preload', () => {
  // A fresh route tree and screen module each time: a lazy screen loads only once.
  async function preloadMail(features: Feature[]): Promise<void> {
    vi.resetModules()
    vi.doMock('@/mail/mail-screen.tsx', () => mailScreenModule())
    const { routeTree: tree } = await import('@/router.tsx')
    const { setRouteFeatures } = await import('@/auth/feature-route.tsx')
    setRouteFeatures(new Set(features))
    const router = createRouter({ routeTree: tree, history: createMemoryHistory() })
    await router.preloadRoute({ to: '/mail' })
  }

  it('does not fetch the screen of a feature that is off', async () => {
    stubFetch()
    await preloadMail([])
    expect(loaded.mail).not.toHaveBeenCalled()
  })

  it('fetches the screen of a feature that is on', async () => {
    stubFetch()
    await preloadMail(['mail'])
    expect(loaded.mail).toHaveBeenCalled()
  })
})
