import { useSearch } from '@tanstack/react-router'

import { useAppearance } from '@/auth/auth-context.ts'
import type { Appearance } from '@/api/auth.ts'
import { LG_QUERY, useMediaQuery } from '@/lib/media-query.ts'

import { useCompose } from './compose-context.ts'
import { useTriage } from './triage-context.ts'

export type ReadingPanePosition = Appearance['reading_pane']

/**
 * Where the open message shows: the user's choice (`appearance.reading_pane`), on the right of the
 * list, below it or `off` (over the list, in its place); `wide` whether the screen is (`lg`), and
 * `fullView` whether a message opens in place of the list, with Back: when off, and always on a
 * narrow screen whatever the choice.
 */
export function useReadingPane(): { position: ReadingPanePosition; wide: boolean; fullView: boolean } {
  const position = useAppearance().appearance.reading_pane
  const wide = useMediaQuery(LG_QUERY, true)
  return { position, wide, fullView: !wide || position === 'off' }
}

/**
 * Whether `u` goes back to the list rather than marking unread: a message open in full view
 * (`fullView`), nothing selected, no composer over it. Mark unread then names no shortcut.
 */
export function uGoesBack(fullView: boolean, selected: number, message: string | undefined, composing: boolean): boolean {
  return fullView && selected === 0 && message !== undefined && !composing
}

/** `uGoesBack` for the mail screen as it is now. */
export function useUGoesBack(): boolean {
  const { fullView } = useReadingPane()
  const { message, thread } = useSearch({ from: '/mail' })
  const { selection } = useTriage()
  const { shown } = useCompose()
  return uGoesBack(fullView, selection.size, thread ?? message, shown)
}
