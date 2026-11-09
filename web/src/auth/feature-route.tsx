import type { AsyncRouteComponent } from '@tanstack/react-router'
import type { ComponentType, ReactElement } from 'react'

import type { Feature } from '@/api/auth.ts'
import { useFeature } from '@/auth/auth-context.ts'
import { NotAvailable } from '@/auth/not-available.tsx'

let routeFeatures: ReadonlySet<Feature> | null = null

/** What the instance runs, for route preloads, which run outside React. AuthProvider sets it. */
export function setRouteFeatures(features: ReadonlySet<Feature>): void {
  routeFeatures = features
}

/**
 * A route's screen, or a short note when the instance does not run `feature`. The route stays
 * registered, so links to it still type-check. It preloads the screen only when its feature is
 * on, so a disabled chunk is never fetched.
 */
export function featureRoute(
  feature: Feature,
  Component: ComponentType & { preload?: () => Promise<void> | undefined },
): AsyncRouteComponent<object> {
  function FeatureGate(): ReactElement {
    return useFeature(feature) ? <Component /> : <NotAvailable />
  }
  // Read at call time: a lazy screen drops its preload once its chunk has loaded. Until /me has
  // answered, nothing is preloaded.
  FeatureGate.preload = () => (routeFeatures?.has(feature) ? Component.preload?.() : undefined)
  return FeatureGate
}
