import type { AsyncRouteComponent } from '@tanstack/react-router'
import type { ComponentType, ReactElement } from 'react'

import type { Feature } from '@/api/auth.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { NotAvailable } from '@/auth/not-available.tsx'

/** The features that write mail, and so leave an audit trail: the Activity page's gate. */
export const WRITES_MAIL: readonly Feature[] = ['mail', 'mcp', 'filters']

let routeFeatures: ReadonlySet<Feature> | null = null

/** What the instance runs, for route preloads, which run outside React. AuthProvider sets it. */
export function setRouteFeatures(features: ReadonlySet<Feature>): void {
  routeFeatures = features
}

/**
 * A route's screen, or a short note when the instance runs none of `features` (one, or any of
 * several). The route stays registered, so links to it still type-check. It preloads the screen
 * only when one of them is on, so a disabled chunk is never fetched.
 */
export function featureRoute(
  features: Feature | readonly Feature[],
  Component: ComponentType & { preload?: () => Promise<void> | undefined },
): AsyncRouteComponent<object> {
  const needed: readonly Feature[] = typeof features === 'string' ? [features] : features
  const served = (running: ReadonlySet<Feature>): boolean => needed.some((feature) => running.has(feature))
  function FeatureGate(): ReactElement {
    return served(useAuth().features) ? <Component /> : <NotAvailable />
  }
  // Read at call time: a lazy screen drops its preload once its chunk has loaded. Until /me has
  // answered, nothing is preloaded.
  FeatureGate.preload = () => (routeFeatures !== null && served(routeFeatures) ? Component.preload?.() : undefined)
  return FeatureGate
}
