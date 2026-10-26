import { useCallback, useEffect, useRef, useState } from 'react'

export type Loadable<T> = { kind: 'loading' } | { kind: 'failed'; message: string } | { kind: 'loaded'; value: T }

/**
 * What `load` resolves to, loaded again when `load` (keep it in `useCallback`) or `reloadKey`
 * changes, or on `retry`; the request before is aborted. A reload keeps showing what was there
 * until it answers.
 */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  failed: (error: unknown) => string,
  reloadKey?: unknown,
): { state: Loadable<T>; retry: () => void } {
  const [state, setState] = useState<Loadable<T>>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  // The wording of a failure follows the language without reloading.
  const failedRef = useRef(failed)
  useEffect(() => {
    failedRef.current = failed
  })

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal).then(
      (value) => {
        setState({ kind: 'loaded', value })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'failed', message: failedRef.current(error) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [load, attempt, reloadKey])

  const retry = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])
  return { state, retry }
}
