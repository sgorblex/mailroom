import { render } from '@testing-library/react'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { vi } from 'vitest'

import { FiltersScreen } from '@/filters/filters-screen.tsx'
import { RuleSetRoute } from '@/filters/rule-set-screen.tsx'

/** The filters screens at `path`, in a router of their own, with Accounts as a placeholder. */
export function renderFiltersApp(path: string): void {
  const rootRoute = createRootRoute({ component: Outlet })
  const accountsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/accounts',
    component: () => <p>Accounts page</p>,
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
  const router = createRouter({
    routeTree: rootRoute.addChildren([accountsRoute, filtersRoute, ruleSetRoute]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  render(<RouterProvider router={router} />)
}
