import type { ReactElement } from 'react'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, type RouterHistory } from '@tanstack/react-router'
import { vi } from 'vitest'

import { validateActivitySearch } from '@/activity/activity-search.ts'
import type { Feature, UserJson } from '@/api/auth.ts'

import { renderSignedIn } from './signed-in.tsx'

/**
 * `ui` at `path`'s page, in a router of its own whose other pages are a line of text each;
 * signed in as `user`. Its history.
 */
export function renderRouted(ui: ReactElement, user: UserJson, path: '/profile' | `/activity${string}`, features?: Feature[]): RouterHistory {
  const rootRoute = createRootRoute({ component: Outlet })
  const page = (name: string): (() => ReactElement) => () => (path.split('?')[0] === name ? ui : <p>Page {name}</p>)
  const profileRoute = createRoute({ getParentRoute: () => rootRoute, path: '/profile', component: page('/profile') })
  const activityRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/activity',
    validateSearch: validateActivitySearch,
    component: page('/activity'),
  })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree: rootRoute.addChildren([profileRoute, activityRoute]), history })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  renderSignedIn(<RouterProvider router={router} />, user, features)
  return history
}
