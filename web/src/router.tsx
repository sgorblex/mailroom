import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router'

import { AccountsScreen } from '@/accounts/accounts-screen.tsx'
import { featureRoute, WRITES_MAIL } from '@/auth/feature-route.tsx'
import { RootLayout } from '@/auth/root-layout.tsx'
import { validateFilterDraftSearch } from '@/filters/draft-search.ts'
import { HealthScreen } from '@/health/health-screen.tsx'
import { validateMailSearch } from '@/mail/route-search.ts'
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
  component: featureRoute(
    'filters',
    lazyRouteComponent(() => import('@/filters/filters-screen.tsx'), 'FiltersScreen'),
  ),
})

// A filter drafted from a message or a search: the static `/filters/new` outranks `/filters/$ruleSetId`.
const filterDraftRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/filters/new',
  validateSearch: validateFilterDraftSearch,
  component: featureRoute(
    'filters',
    lazyRouteComponent(() => import('@/filters/draft-screen.tsx'), 'FilterDraftRoute'),
  ),
})

const ruleSetRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/filters/$ruleSetId',
  component: featureRoute(
    'filters',
    lazyRouteComponent(() => import('@/filters/rule-set-screen.tsx'), 'RuleSetRoute'),
  ),
})

// The mail screens load when first opened, as the filter screens do. Where the reader is (account,
// folder, message, search) lives in the query string.
const mailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/mail',
  validateSearch: validateMailSearch,
  component: featureRoute('mail', lazyRouteComponent(() => import('@/mail/mail-screen.tsx'), 'MailScreen')),
})

// What was done to the user's mail: wherever the instance writes it (mail, MCP or filters).
const activityRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/activity',
  component: featureRoute(
    WRITES_MAIL,
    lazyRouteComponent(() => import('@/activity/activity-screen.tsx'), 'ActivityScreen'),
  ),
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
  component: featureRoute('mcp', ConnectRoute),
})

export const routeTree = rootRoute.addChildren([
  indexRoute,
  accountsRoute,
  mailRoute,
  filtersRoute,
  filterDraftRoute,
  ruleSetRoute,
  activityRoute,
  usersRoute,
  profileRoute,
  connectRoute,
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
