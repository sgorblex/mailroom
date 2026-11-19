import { render } from '@testing-library/react'
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

import { FilterDraftRoute } from '@/filters/draft-screen.tsx'
import { validateFilterDraftSearch } from '@/filters/draft-search.ts'
import { FiltersScreen } from '@/filters/filters-screen.tsx'
import { RuleSetRoute } from '@/filters/rule-set-screen.tsx'

/** The filters screens at `path`, in a router of their own, with Accounts and Mail as placeholders; its history. */
export function renderFiltersApp(path: string): RouterHistory {
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
  render(<RouterProvider router={router} />)
  return history
}
