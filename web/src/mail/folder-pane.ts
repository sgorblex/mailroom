import { useCallback } from 'react'

import { readDevicePref, useDevicePref, writeDevicePref } from '@/lib/device-prefs.ts'
import { LG_QUERY, matchesMedia, useMediaQuery, XL_QUERY } from '@/lib/media-query.ts'

/**
 * Whether the folder pane is folded to its rail, this device's choice: `auto` folds it below `xl`
 * only; a toggle that ends where `auto` would at that width goes back to `auto`, so a pane opened
 * on a laptop and folded again folds on its own once more.
 */
export type FolderPaneChoice = 'auto' | 'collapsed' | 'expanded'

const PANE_PREF = 'mail.folderPane'
const AUTO: FolderPaneChoice = 'auto'
const isChoice = (value: unknown): value is FolderPaneChoice =>
  value === 'auto' || value === 'collapsed' || value === 'expanded'

/** Whether the pane is a rail: only on a wide screen (`lg`), where it sits beside the list. */
export function isCollapsed(choice: FolderPaneChoice, wide: boolean, roomy: boolean): boolean {
  return wide && (choice === 'collapsed' || (choice === 'auto' && !roomy))
}

/** The choice that turns the pane over from `collapsed`, on a screen `roomy` (`xl`) or not. */
export function toggled(collapsed: boolean, roomy: boolean): FolderPaneChoice {
  const folding = !collapsed
  if (folding === !roomy) {
    return 'auto'
  }
  return folding ? 'collapsed' : 'expanded'
}

/** Folds or opens the folder pane, as `[` does; `false` on a narrow screen, where it is no rail. */
export function toggleFolderPane(): boolean {
  const wide = matchesMedia(LG_QUERY, true)
  if (!wide) {
    return false
  }
  const roomy = matchesMedia(XL_QUERY, true)
  const choice = readDevicePref(PANE_PREF, AUTO, isChoice)
  writeDevicePref(PANE_PREF, toggled(isCollapsed(choice, wide, roomy), roomy))
  return true
}

/** Whether the folder pane is folded now, whether the screen is wide (`lg`), and the toggle. */
export function useFolderPane(): { collapsed: boolean; wide: boolean; toggle: () => void } {
  const [choice, setChoice] = useDevicePref(PANE_PREF, AUTO, isChoice)
  const wide = useMediaQuery(LG_QUERY, true)
  const roomy = useMediaQuery(XL_QUERY, true)
  const collapsed = isCollapsed(choice, wide, roomy)
  const toggle = useCallback(() => {
    setChoice(toggled(collapsed, roomy))
  }, [setChoice, collapsed, roomy])
  return { collapsed, wide, toggle }
}
