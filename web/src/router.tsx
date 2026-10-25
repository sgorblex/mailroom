import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'

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

const profileRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/profile',
  component: ProfileScreen,
})

export const router = createRouter({
  routeTree: rootRoute.addChildren([indexRoute, accountsRoute, usersRoute, profileRoute]),
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
