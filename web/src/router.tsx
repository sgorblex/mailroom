import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router'

import { AccountsScreen } from '@/accounts/accounts-screen.tsx'
import { RootLayout } from '@/auth/root-layout.tsx'
import { HealthScreen } from '@/health/health-screen.tsx'
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

export const router = createRouter({
  routeTree: rootRoute.addChildren([
    indexRoute,
    accountsRoute,
    filtersRoute,
    ruleSetRoute,
    usersRoute,
    profileRoute,
  ]),
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
