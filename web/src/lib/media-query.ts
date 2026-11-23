import { useCallback, useSyncExternalStore } from 'react'

/** Tailwind's `lg` and `xl` breakpoints, as media queries. */
export const LG_QUERY = '(min-width: 64rem)'
export const XL_QUERY = '(min-width: 80rem)'
/** The OS asks for dark mode. */
export const DARK_QUERY = '(prefers-color-scheme: dark)'

/** Whether the window matches `query` now; `fallback` where it cannot tell (no `matchMedia`). */
export function matchesMedia(query: string, fallback: boolean): boolean {
  return typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : fallback
}

/** Whether the window matches `query`, following it as it changes; `fallback` where it cannot tell. */
export function useMediaQuery(query: string, fallback: boolean): boolean {
  const subscribe = useCallback(
    (listener: () => void) => {
      if (typeof window.matchMedia !== 'function') {
        return () => undefined
      }
      const media = window.matchMedia(query)
      media.addEventListener('change', listener)
      return () => {
        media.removeEventListener('change', listener)
      }
    },
    [query],
  )
  return useSyncExternalStore(subscribe, () => matchesMedia(query, fallback))
}
