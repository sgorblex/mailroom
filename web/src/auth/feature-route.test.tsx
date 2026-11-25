import { render, screen } from '@testing-library/react'
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type Feature } from '@/api/auth.ts'
import { mailOpening, OPENING_PREF, type Opening } from '@/auth/opening.ts'
import { OpeningSync } from '@/auth/opening-sync.tsx'
import { routeTree } from '@/router.tsx'
import { renderSignedIn } from '@/test/signed-in.tsx'

const loaded = vi.hoisted(() => ({ mail: vi.fn(), filters: vi.fn(), activity: vi.fn() }))

function mailScreenModule(): { MailScreen: () => ReactElement } {
  loaded.mail()
  return { MailScreen: () => <p>Mail screen</p> }
}

vi.mock('@/mail/mail-screen.tsx', () => mailScreenModule())
vi.mock('@/activity/activity-screen.tsx', () => {
  loaded.activity()
  return { ActivityScreen: () => <p>Activity screen</p> }
})
vi.mock('@/filters/rule-set-screen.tsx', () => {
  loaded.filters()
  return { RuleSetRoute: () => <p>Rule set screen</p> }
})

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

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
  loaded.activity.mockClear()
})

describe('featureRoute', () => {
  it.each([
    { path: '/mail', module: 'mail' as const },
    { path: '/filters/1', module: 'filters' as const },
    { path: '/activity', module: 'activity' as const },
  ])('says $path is not available, never loading its screen', async ({ path, module }) => {
    stubFetch()
    renderApp(path, [])
    expect(
      await screen.findByRole('heading', { name: 'Not available on this instance' }),
    ).toBeInTheDocument()
    expect(loaded[module]).not.toHaveBeenCalled()
  })

  it('shows a screen of several features when any of them is on', async () => {
    stubFetch()
    renderApp('/activity', ['filters'])
    expect(await screen.findByText('Activity screen')).toBeInTheDocument()
    expect(loaded.activity).toHaveBeenCalled()
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
    // Only the deploy banner's read of the version, never the consent request.
    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls.filter((url) => !url.endsWith('/api/v1/version'))).toEqual([])
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

describe('featureRoute on a cold load', () => {
  /**
   * The app as main.tsx mounts it, on `/mail`, fresh: the router's first load runs before /me
   * has answered, and the screen's code takes a moment to arrive.
   */
  async function coldLoad(me: () => Promise<Response>): Promise<void> {
    vi.resetModules()
    vi.doMock('@/mail/mail-screen.tsx', async () => {
      await new Promise((resolve) => setTimeout(resolve, 30))
      return mailScreenModule()
    })
    const { routeTree: tree } = await import('@/router.tsx')
    const { AuthProvider } = await import('@/auth/auth-provider.tsx')
    // Under the cache, as main.tsx mounts it: the live updates read it.
    const { QueryClientProvider } = await import('@tanstack/react-query')
    const { createQueryClient } = await import('@/lib/query-client.ts')
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === '/api/v1/auth/me' ? me() : Promise.resolve(new Response('{}', { status: 404 })),
      ),
    )
    vi.stubGlobal('scrollTo', vi.fn())
    const router = createRouter({ routeTree: tree, history: createMemoryHistory({ initialEntries: ['/mail'] }) })
    render(
      <QueryClientProvider client={createQueryClient()}>
        <AuthProvider>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>,
    )
  }

  const answer = (features: Feature[]) => (): Promise<Response> =>
    new Promise((resolve) => {
      setTimeout(() => {
        resolve(new Response(JSON.stringify({ user: ALICE, features }), { status: 200 }))
      }, 10)
    })

  it('shows the screen once its code is in, without a use() error', async () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      await coldLoad(answer(['mail']))
      expect(await screen.findByText('Mail screen')).toBeInTheDocument()
      const messages = errors.mock.calls.map((call) => String(call[0]))
      expect(messages.filter((message) => message.includes('use()'))).toEqual([])
    } finally {
      errors.mockRestore()
    }
  })

  it('never fetches the screen of a feature that is off', async () => {
    await coldLoad(answer([]))
    expect(
      await screen.findByRole('heading', { name: 'Not available on this instance' }),
    ).toBeInTheDocument()
    expect(loaded.mail).not.toHaveBeenCalled()
  })
})

describe('the mail screen on opening', () => {
  const remember = (opening: Opening): void => {
    window.localStorage.setItem(`mailroom.ui.${OPENING_PREF}`, JSON.stringify(opening))
  }

  it.each([
    { path: '/mail', firstPage: true, remembered: null, opens: true },
    { path: '/mail', firstPage: false, remembered: { home: 'dashboard', mail: true }, opens: true },
    { path: '/mail', firstPage: true, remembered: { home: 'mail', mail: false }, opens: false },
    { path: '/', firstPage: true, remembered: { home: 'mail', mail: true }, opens: true },
    { path: '/', firstPage: false, remembered: { home: 'mail', mail: true }, opens: false },
    { path: '/', firstPage: true, remembered: { home: 'mail', mail: false }, opens: false },
    { path: '/', firstPage: true, remembered: { home: 'filters', mail: true }, opens: false },
    { path: '/', firstPage: true, remembered: null, opens: false },
    { path: '/filters', firstPage: true, remembered: { home: 'mail', mail: true }, opens: false },
    { path: '/mail/x', firstPage: true, remembered: null, opens: false },
  ] satisfies { path: string; firstPage: boolean; remembered: Opening | null; opens: boolean }[])(
    '$path (first page $firstPage, remembered $remembered) opens on mail: $opens',
    ({ path, firstPage, remembered, opens }) => {
      expect(mailOpening(path, firstPage, remembered)).toBe(opens)
    },
  )

  // A fresh module each time: an import already done would not run the mock's factory again.
  async function preload(path: string, firstPage: boolean): Promise<void> {
    vi.resetModules()
    vi.doMock('@/mail/mail-screen.tsx', () => mailScreenModule())
    const { preloadMailOpening } = await import('@/auth/opening.ts')
    preloadMailOpening(path, firstPage)
    await new Promise((resolve) => setTimeout(resolve, 0))
  }

  it('fetches the mail screen before /me when the load opens on it', async () => {
    remember({ home: 'mail', mail: true })
    await preload('/', true)
    expect(loaded.mail).toHaveBeenCalled()
  })

  it('fetches it at /mail before anything is remembered', async () => {
    await preload('/mail', true)
    expect(loaded.mail).toHaveBeenCalled()
  })

  it('never fetches it where the instance last ran no mail', async () => {
    remember({ home: 'mail', mail: false })
    await preload('/mail', true)
    expect(loaded.mail).not.toHaveBeenCalled()
  })

  it.each(['/filters', '/activity', '/profile', '/'])('never fetches it at %s', async (path) => {
    remember({ home: 'dashboard', mail: true })
    await preload(path, true)
    expect(loaded.mail).not.toHaveBeenCalled()
  })

  it('reads a remembered value it does not know as nothing remembered', async () => {
    window.localStorage.setItem(`mailroom.ui.${OPENING_PREF}`, JSON.stringify({ home: 'elsewhere', mail: true }))
    await preload('/', true)
    expect(loaded.mail).not.toHaveBeenCalled()
  })

  it('remembers the signed-in user’s home and whether the instance runs mail', () => {
    renderSignedIn(<OpeningSync />, { ...ALICE, appearance: { ...DEFAULT_APPEARANCE, home: 'mail' } }, ['filters'])
    expect(JSON.parse(window.localStorage.getItem(`mailroom.ui.${OPENING_PREF}`) ?? 'null')).toEqual({
      home: 'mail',
      mail: false,
    })
  })
})
