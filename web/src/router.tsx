import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router'

import { AccountsScreen } from '@/accounts/accounts-screen.tsx'
import { RootLayout } from '@/auth/root-layout.tsx'
import { HealthScreen } from '@/health/health-screen.tsx'
import { ConnectRoute } from '@/oauth/connect-screen.tsx'
import { ProfileScreen } from '@/profile/profile-screen.tsx'
import { UsersScreen } from '@/users/users-screen.tsx'

const rootRoute = createRootRoute({ component: RootLayout })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: HealthScreen,
})

const usersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/users',
  component: UsersScreen,
})

const accountsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/accounts',
  component: AccountsScreen,
})

// The filter screens load when first opened: they would make the first load much heavier.
const filtersRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/filters',
  component: lazyRouteComponent(() => import('@/filters/filters-screen.tsx'), 'FiltersScreen'),
})

const ruleSetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/filters/$ruleSetId',
  component: lazyRouteComponent(() => import('@/filters/rule-set-screen.tsx'), 'RuleSetRoute'),
})

const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profile',
  component: ProfileScreen,
})

// Where the built-in OAuth server sends a user to approve an app (MCP client).
const connectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/connect',
  validateSearch: ({ request }: Record<string, unknown>): { request?: string } =>
    typeof request === 'string' ? { request } : {},
  component: ConnectRoute,
})

export const router = createRouter({
  routeTree: rootRoute.addChildren([
    indexRoute,
    accountsRoute,
    filtersRoute,
    ruleSetRoute,
    usersRoute,
    profileRoute,
    connectRoute,
  ]),
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
