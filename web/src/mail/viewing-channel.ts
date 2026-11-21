import { useCallback, useEffect, useRef } from 'react'

import type { AccountId } from '@/api/accounts.ts'

import type { Viewing } from './live-context.ts'
import { isSameFolder } from './triage-context.ts'

const CHANNEL = 'mailroom-viewing'

/** This tab's name on the channel. */
const TAB = Math.random().toString(36).slice(2)

/** What a tab says over the channel: the folder it shows while focused, null otherwise. */
interface Announcement {
  tab: string
  viewing: Viewing | null
}

function isAnnouncement(data: unknown): data is Announcement {
  if (typeof data !== 'object' || data === null) {
    return false
  }
  const { tab, viewing } = data as Record<string, unknown>
  if (typeof tab !== 'string') {
    return false
  }
  if (typeof viewing !== 'object' || viewing === null) {
    return viewing === null
  }
  const { account, folder } = viewing as { account?: unknown; folder?: unknown }
  return typeof account === 'number' && typeof folder === 'string'
}

/** Whether this tab is the one the user looks at. */
function focusedHere(): boolean {
  return document.visibilityState === 'visible' && document.hasFocus()
}

/**
 * Whether the user sees `account`'s `folder` in the focused Mailroom tab, this one or another.
 * This tab announces `viewing` over `BroadcastChannel('mailroom-viewing')` while it is visible and
 * focused (null on blur, when hidden, on unload) and keeps what the focused tab announces. Without
 * BroadcastChannel only this tab counts.
 */
export function useFocusedViewing(viewing: Viewing | null): (account: AccountId, folder: string) => boolean {
  const channel = useRef<BroadcastChannel | null>(null)
  const other = useRef<Announcement | null>(null)
  const latest = useRef(viewing)
  const announced = useRef<string | null>(null)

  const announce = useCallback((leaving = false) => {
    const shown = !leaving && focusedHere() ? latest.current : null
    const key = shown === null ? null : `${String(shown.account)}/${shown.folder}`
    if (channel.current === null || key === announced.current) {
      return
    }
    announced.current = key
    channel.current.postMessage({ tab: TAB, viewing: shown } satisfies Announcement)
  }, [])

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') {
      return
    }
    const opened = new BroadcastChannel(CHANNEL)
    channel.current = opened
    opened.addEventListener('message', (message: MessageEvent<unknown>) => {
      const data = message.data
      if (!isAnnouncement(data)) {
        return
      }
      if (data.viewing !== null) {
        other.current = data
      } else if (other.current?.tab === data.tab) {
        // Only the tab that announced a folder takes it back: another's blur may come after.
        other.current = null
      }
    })
    const changed = (): void => {
      announce()
    }
    const leave = (): void => {
      announce(true)
    }
    window.addEventListener('focus', changed)
    window.addEventListener('blur', leave)
    document.addEventListener('visibilitychange', changed)
    window.addEventListener('pagehide', leave)
    announce()
    return () => {
      announce(true)
      window.removeEventListener('focus', changed)
      window.removeEventListener('blur', leave)
      document.removeEventListener('visibilitychange', changed)
      window.removeEventListener('pagehide', leave)
      opened.close()
      channel.current = null
      announced.current = null
    }
  }, [announce])

  const account = viewing?.account
  const folder = viewing?.folder
  useEffect(() => {
    latest.current = account === undefined || folder === undefined ? null : { account, folder }
    announce()
  }, [announce, account, folder])

  return useCallback((account: AccountId, folder: string): boolean => {
    const here = focusedHere() ? latest.current : null
    const shown = [here, other.current?.viewing ?? null]
    return shown.some((seen) => seen?.account === account && isSameFolder(seen.folder, folder))
  }, [])
}
