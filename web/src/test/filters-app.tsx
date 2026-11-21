import { render } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  type RouterHistory,
} from '@tanstack/react-router'
import { vi } from 'vitest'

import type { Feature } from '@/api/auth.ts'
import { FilterDraftRoute } from '@/filters/draft-screen.tsx'
import { validateFilterDraftSearch } from '@/filters/draft-search.ts'
import { FiltersScreen } from '@/filters/filters-screen.tsx'
import { RuleSetRoute } from '@/filters/rule-set-screen.tsx'
import { createQueryClient } from '@/lib/query-client.ts'

import { renderSignedIn } from './signed-in.tsx'

/**
 * The filters screens at `path`, in a router of their own, with Accounts and Mail as placeholders;
 * its history. With `features`, signed in on an instance with them (else no `AuthProvider`).
 */
export function renderFiltersApp(path: string, { features }: { features?: Feature[] } = {}): RouterHistory {
  const rootRoute = createRootRoute({ component: Outlet })
  const accountsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/accounts',
    component: () => <p>Accounts page</p>,
  })
  const mailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/mail',
    component: () => <p>Mail page</p>,
  })
  const filterDraftRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/filters/new',
    validateSearch: validateFilterDraftSearch,
    component: FilterDraftRoute,
  })
  const filtersRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/filters',
    component: FiltersScreen,
  })
  const ruleSetRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/filters/$ruleSetId',
    component: RuleSetRoute,
  })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({
    routeTree: rootRoute.addChildren([accountsRoute, mailRoute, filtersRoute, filterDraftRoute, ruleSetRoute]),
    history,
  })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  const app = <RouterProvider router={router} />
  if (features === undefined) {
    render(<QueryClientProvider client={createQueryClient()}>{app}</QueryClientProvider>)
  } else {
    renderSignedIn(app, { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true }, features)
  }
  return history
}
