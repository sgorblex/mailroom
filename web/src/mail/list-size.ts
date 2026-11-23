/** The message list's size on a wide screen, beside or above the reading pane: bounds and defaults. */

import type { Appearance } from '@/api/auth.ts'
import { clamp } from '@/lib/number.ts'

/**
 * A row's height before it is measured, per density: a sender, a subject and an opening, or
 * without the opening and with less padding.
 */
export const ROW_ESTIMATE: Readonly<Record<Appearance['density'], number>> = { comfortable: 76, compact: 50 }

/** The message list's width on a wide screen, in pixels: by default, and the narrowest and widest. */
export const LIST_WIDTH = 480
export const LIST_MIN = 320
export const LIST_MAX = 960
/** What the reading pane keeps however wide the list is made: a readable measure, and room for its
 * action bar's first actions. */
export const READING_MIN = 480
/** With the reading pane below it, the list's height: the lowest and highest whatever the room, and
 * what the pane keeps. */
export const LIST_MIN_HEIGHT = 160
export const LIST_MAX_HEIGHT = 1200
export const READING_MIN_HEIGHT = 240
/** With the pane below, the rows the list keeps room for when it can, and its share of the room
 * until this device chooses a height. */
export const LIST_MIN_ROWS = 5
export const LIST_HEIGHT_SHARE = 0.4
/** With the pane below, the list pane's height besides its rows: the one-row toolbar, Select all,
 * the padding and the gaps between. */
export const LIST_CHROME_HEIGHT = 96

/** A size's bounds and where it starts until this device chooses one. */
export interface PaneBounds { min: number; max: number; initial: number }

/** The list's width beside the reading pane, sharing `room` pixels (`null` before it is laid out). */
export function listWidthBounds(room: number | null): PaneBounds {
  const max = room === null ? LIST_MAX : clamp(room - READING_MIN, LIST_MIN, LIST_MAX)
  return { min: LIST_MIN, max, initial: LIST_WIDTH }
}

/**
 * The list's height above the reading pane, sharing `room` pixels (`null` before it is laid out),
 * with rows `rowHeight` high: at least `LIST_MIN_ROWS` rows, unless the pane would have less than
 * its own minimum then, and by default `LIST_HEIGHT_SHARE` of the room, if that holds them.
 */
export function listHeightBounds(room: number | null, rowHeight: number): PaneBounds {
  const rows = LIST_CHROME_HEIGHT + LIST_MIN_ROWS * rowHeight
  const max = room === null ? LIST_MAX_HEIGHT : clamp(room - READING_MIN_HEIGHT, LIST_MIN_HEIGHT, LIST_MAX_HEIGHT)
  const min = clamp(rows, LIST_MIN_HEIGHT, max)
  return { min, max, initial: room === null ? min : clamp(Math.round(room * LIST_HEIGHT_SHARE), min, max) }
}
