import { useSyncExternalStore } from 'react'

import { dayIn } from './format.ts'

const MINUTE_MS = 60_000

const listeners = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | undefined

function notify(): void {
  for (const listener of listeners) {
    listener()
  }
}

/** Looks again as each minute begins: a day begins on one in every time zone. */
function schedule(): void {
  timer = setTimeout(() => {
    schedule()
    notify()
  }, MINUTE_MS - (Date.now() % MINUTE_MS))
}

/** One timer for every subscriber: each row of a list is one. */
function subscribe(listener: () => void): () => void {
  if (listeners.size === 0) {
    schedule()
    // Timers wait while the device sleeps.
    document.addEventListener('visibilitychange', notify)
  }
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) {
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', notify)
    }
  }
}

/** The calendar day it is in `timeZone` (`dayIn`), drawing again when it changes. */
export function useToday(timeZone: string | undefined): string {
  return useSyncExternalStore(subscribe, () => dayIn(new Date(), timeZone))
}
