import { act, screen, within } from '@testing-library/react'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  type AnyRouter,
} from '@tanstack/react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type Appearance, type Feature, type UserJson } from '@/api/auth.ts'
import type { HomeSummary } from '@/api/home.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { HomeRoute } from './home-screen.tsx'

const SUMMARY: HomeSummary = {
  mail: {
    inboxes: [
      { account_id: 1, display_name: 'Work', colour: 'blue', unread: 3 },
      { account_id: 2, display_name: 'Home', colour: null, unread: null },
    ],
    unread: 3,
    starred: 5,
    handled: 2,
    snoozed: 4,
    snoozed_today: 1,
    scheduled: 2,
    next_scheduled_at: '2026-11-21T08:00:00Z',
    scheduled_failed: 1,
  },
  filters: {
    rule_sets: [
      {
        id: 7,
        name: 'Newsletters',
        account_id: 1,
        enabled: true,
        disabled_reason: null,
        runs: 12,
        failed_runs: 2,
        last_run_at: '2026-11-20T21:00:00Z',
        last_run_failed: true,
        acted_on: 30,
      },
      {
        id: 8,
        name: 'Bills',
        account_id: null,
        enabled: false,
        disabled_reason: null,
        runs: 0,
        failed_runs: 0,
        last_run_at: null,
        last_run_failed: false,
        acted_on: 0,
      },
    ],
  },
}

function user(home: Appearance['home'] = 'dashboard'): UserJson {
  return {
    username: 'alice',
    is_admin: false,
    time_zone: 'Europe/Rome',
    mark_read_subfolders: true,
    index_max_age_days: null,
    conversations: true,
    undo_send_seconds: 10,
    appearance: { ...DEFAULT_APPEARANCE, home },
  }
}

function stubApi(summary: HomeSummary): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string) =>
    Promise.resolve(
      path === '/api/v1/home' ? new Response(JSON.stringify(summary), { status: 200 }) : new Response('{"detail":"Not Found"}', { status: 404 }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Home at `/`, every other page a line of text naming it. */
function renderHome(home: Appearance['home'] = 'dashboard', features?: Feature[]): AnyRouter {
  const rootRoute = createRootRoute({ component: Outlet })
  const homeRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: HomeRoute })
  const pages = ['/mail', '/filters', '/filters/$ruleSetId', '/accounts', '/activity', '/profile', '/health'].map((path) =>
    createRoute({ getParentRoute: () => rootRoute, path, component: () => <p>Page {path}</p> }),
  )
  const router = createRouter({
    routeTree: rootRoute.addChildren([homeRoute, ...pages]),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  vi.stubGlobal('scrollTo', vi.fn())
  renderSignedIn(<RouterProvider router={router} />, user(home), features)
  return router
}

const href = (scope: HTMLElement, name: RegExp | string): string =>
  decodeURIComponent(within(scope).getByRole('link', { name }).getAttribute('href') ?? '')

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HomeScreen', () => {
  it('passes axe', async () => {
    stubApi(SUMMARY)
    renderHome()
    await screen.findByRole('region', { name: 'Unread' })
    await expectNoAxeViolations()
  })

  it('counts unread mail per inbox, linking each and All inboxes', async () => {
    stubApi(SUMMARY)
    renderHome()
    const unread = await screen.findByRole('region', { name: 'Unread' })
    expect(within(unread).getByRole('link', { name: /All inboxes/ })).toHaveTextContent('All inboxes3')
    expect(href(unread, /All inboxes/)).toBe('/mail?view=all_inboxes')
    expect(href(unread, /Work/)).toBe('/mail?account=1&folder=INBOX')
    // Not indexed yet: no number, said so.
    expect(within(unread).getByRole('link', { name: /Home/ })).toHaveTextContent('Not counted yet: the inbox is not indexed')
  })

  it('shows what waits on the user, each linking to its list', async () => {
    stubApi(SUMMARY)
    renderHome()
    const waiting = await screen.findByRole('region', { name: 'Waiting on you' })
    expect(within(waiting).getByRole('link', { name: /Starred/ })).toHaveTextContent('5')
    expect(href(waiting, /Starred/)).toContain('"flagged":true')
    expect(href(waiting, /Handled/)).toContain('"handled":true')
    const snoozed = within(waiting).getByRole('link', { name: /Snoozed/ })
    expect(snoozed).toHaveTextContent('Snoozed, back today4 snoozed in all1')
    expect(href(waiting, /Snoozed/)).toBe('/mail?list=snoozed')
    const scheduled = within(waiting).getByRole('link', { name: /Scheduled/ })
    // In the user's zone: 09:00 in Rome.
    expect(scheduled).toHaveTextContent(/Next: .*9:00/)
    expect(within(scheduled).getByText('1 failed')).toHaveClass('text-destructive')
    expect(href(waiting, /Scheduled/)).toBe('/mail?list=scheduled')
  })

  it("sums up the filters' week, failures highlighted", async () => {
    stubApi(SUMMARY)
    renderHome()
    const filters = await screen.findByRole('region', { name: 'Filters at a glance' })
    const [news, bills] = within(filters).getAllByRole('listitem')
    if (news === undefined || bills === undefined) throw new Error('Two rule sets expected')
    expect(within(news).getByRole('link', { name: 'Newsletters' })).toHaveAttribute('href', '/filters/7')
    expect(news).toHaveTextContent('12 recent runs')
    expect(within(news).getByText('2 failed')).toHaveClass('text-destructive')
    expect(news).toHaveTextContent('30 messages acted on')
    expect(within(news).getByText(/Last run failed/)).toHaveClass('text-destructive')
    expect(bills).toHaveTextContent('Off')
    expect(href(filters, 'Open Filters')).toBe('/filters')
    expect(href(filters, 'See Activity')).toBe('/activity')
  })

  it('leaves out what the instance does not run', async () => {
    stubApi({ ...SUMMARY, mail: null })
    renderHome('dashboard', ['filters'])
    expect(await screen.findByRole('region', { name: 'Filters at a glance' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Unread' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Waiting on you' })).not.toBeInTheDocument()
    const tiles = screen.getByRole('navigation', { name: 'Pages' })
    expect(within(tiles).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/filters',
      '/accounts',
      '/activity',
      '/profile',
    ])
  })

  it('offers a tile per page on a full instance', async () => {
    stubApi(SUMMARY)
    renderHome()
    const tiles = await screen.findByRole('navigation', { name: 'Pages' })
    expect(within(tiles).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/mail',
      '/filters',
      '/accounts',
      '/activity',
      '/profile',
    ])
  })

  it('says so when the summary cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"detail":"Server error"}', { status: 500 }))))
    renderHome()
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error')
    expect(screen.getByRole('navigation', { name: 'Pages' })).toBeInTheDocument()
  })
})

describe('HomeRoute', () => {
  it('opens the page the user chose instead of the dashboard', async () => {
    const fetchMock = stubApi(SUMMARY)
    renderHome('filters')
    expect(await screen.findByText('Page /filters')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/v1/home', expect.anything())
  })

  it('opens the dashboard when Home is chosen later, whatever the page to open on', async () => {
    stubApi(SUMMARY)
    const router = renderHome('filters')
    expect(await screen.findByText('Page /filters')).toBeInTheDocument()
    await act(() => router.navigate({ to: '/' }))
    expect(await screen.findByRole('heading', { name: 'Home', level: 1 })).toBeInTheDocument()
  })

  it('shows the dashboard when the chosen page is not on this instance', async () => {
    stubApi({ ...SUMMARY, mail: null })
    renderHome('mail', ['filters'])
    expect(await screen.findByRole('heading', { name: 'Home', level: 1 })).toBeInTheDocument()
  })
})
