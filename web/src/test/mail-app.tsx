import { StrictMode } from 'react'
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

import { MailScreen } from '@/mail/mail-screen.tsx'
import { validateMailSearch } from '@/mail/route-search.ts'

/** The mail screen at `path`, in a router of its own, with Accounts as a placeholder; its history to inspect. `strict` as `main.tsx` renders it. */
export function renderMailApp(path: string, { strict = false }: { strict?: boolean } = {}): RouterHistory {
  const rootRoute = createRootRoute({ component: Outlet })
  const accountsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/accounts',
    component: () => <p>Accounts page</p>,
  })
  const mailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/mail',
    validateSearch: validateMailSearch,
    component: MailScreen,
  })
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createRouter({ routeTree: rootRoute.addChildren([accountsRoute, mailRoute]), history })
  // The router restores scroll positions, which jsdom does not implement.
  vi.stubGlobal('scrollTo', vi.fn())
  render(<RouterProvider router={router} />, { wrapper: strict ? StrictMode : undefined })
  return history
}
