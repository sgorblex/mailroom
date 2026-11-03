import { useCallback, useEffect, useRef } from 'react'

/**
 * For requests a click starts (as `useLoad` aborts the ones it starts): `signal()` is aborted when
 * the component unmounts, so a request still on its way is dropped with it.
 */
export function useUnmountSignal(): () => AbortSignal {
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    const current = new AbortController()
    controller.current = current
    return () => {
      current.abort()
    }
  }, [])
  return useCallback(() => {
    controller.current ??= new AbortController()
    return controller.current.signal
  }, [])
}
