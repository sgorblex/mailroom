import { use, useRef, useState, type PointerEvent, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Ellipsis, type LucideIcon } from 'lucide-react'

import type { Appearance } from '@/api/auth.ts'
import type { MessageItem } from '@/api/mail.ts'
import { useAccountHandled } from '@/accounts/accounts-context.ts'
import { useAppearance } from '@/auth/auth-context.ts'
import { cn } from '@/lib/utils.ts'

import { MAIL_ACTIONS, unavailableReason, type ActionContext } from './action-registry.ts'
import { ComposeContext } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { useMuting } from './mute-context.ts'
import { rowActions } from './row-action-items.ts'
import { RowActions } from './row-actions.tsx'
import { useSnoozing } from './snooze-context.ts'
import { useTriageActions, type TriageActions } from './triage-actions.ts'
import { useTriage, type Action } from './triage-context.ts'

/** How far a finger moves before the swipe is known to go sideways or up and down. */
const SLOP = 10
/** How much of the row's width a swipe must cover to act. */
export const SWIPE_SHARE = 0.35
/** A shorter swipe still acts when the finger leaves this fast (px per ms) and has moved `FLING_MIN`. */
const FLING_SPEED = 0.5
const FLING_MIN = 40
/** A finger held still this long before it lifts flings nothing. */
const STILL_MS = 100
/** A pointer click this soon after a swipe ends is the swipe's own, not a tap. */
const SWIPE_CLICK_MS = 500

type SwipeKind = Appearance['swipe_left']

/** Unless a row action composes: a stub spares each row the composer's re-renders. */
const NO_COMPOSE: ActionContext['compose'] = { start: () => undefined, canCompose: () => false }

/** What a swipe to one side does to the row: the registry's action, and what it runs. */
interface Swipe {
  label: string
  icon: LucideIcon
  destructive: boolean
  /** The triage action it runs, or `menu`: it shows the row's actions. */
  action: Action | 'menu'
  /** The row leaves the list (a move): it stays swiped away until the action ends. */
  leaves: boolean
}

interface Gesture {
  pointer: number
  x: number
  y: number
  width: number
  /** Unknown until the finger has moved `SLOP`. */
  sideways: boolean | null
  lastX: number
  lastAt: number
  speed: number
  /** How far the row is moved: towards a side that acts only. */
  dx: number
}

/** The registry's action a swipe of `kind` runs on the row, and the triage action that is; `null` when it cannot be run on it. */
function swipeOf(kind: Exclude<SwipeKind, 'menu'>, actions: TriageActions): { id: string; action: Action } | null {
  switch (kind) {
    case 'none':
      return null
    case 'archive':
      return { id: 'archive', action: { kind: 'archive' } }
    case 'trash':
      return { id: 'trash', action: { kind: 'trash' } }
    case 'star':
      return actions.star === null ? null : { id: 'star', action: { kind: 'flag', change: { flagged: actions.star.flagged } } }
    case 'handled':
      return actions.handled === null ? null : { id: 'handled', action: { kind: 'flag', change: { handled: actions.handled.handled } } }
    case 'read':
      return actions.read === null
        ? null
        : { id: actions.read.seen ? 'markRead' : 'markUnread', action: { kind: 'flag', change: { seen: actions.read.seen } } }
  }
}

/**
 * A message row that a finger swipes left or right to run the action the user chose for that side
 * (`appearance.swipe_left`, `swipe_right`) on this message alone, through the triage path: its
 * outcome and Undo are the toolbar's. Only touch swipes: a mouse drags the row to a folder. The
 * browser keeps vertical scrolling (`touch-action: pan-y`); a swipe is sideways only once the
 * finger has gone further sideways than up or down. While it moves, the action shows behind the
 * row, in its colour once letting go would run it: past `SWIPE_SHARE` of the width, or flung.
 * Short of that the row goes back. A side whose action is none, or unavailable here, does not move.
 * Over the row's end come its `RowActions`, on hover or focus, and where a side's action is `menu`,
 * once swiped to it.
 */
export function SwipeRow({
  message,
  compact,
  children,
}: {
  message: MessageItem
  compact: boolean
  children: ReactNode
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { swipe_left, swipe_right, row_actions } = useAppearance().appearance
  const triage = useTriage()
  const actions = useTriageActions([message])
  const composes = row_actions.some((kind) => kind === 'reply' || kind === 'forward')
  const compose = composes ? use(ComposeContext) : null
  const muting = useMuting()
  const snoozing = useSnoozing()
  const context: ActionContext = {
    actions,
    message,
    account: null,
    compose: compose ?? NO_COMPOSE,
    // A picker acts on the selection, so the row becomes it.
    openPicker: () => {
      triage.clearSelection()
      triage.select([message], true)
      triage.setPicker(true)
    },
    muting,
    snoozing,
  }
  const items = rowActions(row_actions, context, useAccountHandled()(message.account_id).kind === 'star')
  const [revealed, setRevealed] = useState(false)
  const closeActions = (): void => {
    setRevealed(false)
  }
  const sideOf = (kind: SwipeKind): Swipe | null => {
    if (kind === 'menu') {
      return items.length === 0
        ? null
        : { label: t('list.rowActions'), icon: Ellipsis, destructive: false, action: 'menu', leaves: false }
    }
    const swipe = swipeOf(kind, actions)
    const entry = swipe === null ? undefined : MAIL_ACTIONS.find((each) => each.id === swipe.id)
    if (swipe === null || entry === undefined || !entry.available(context) || unavailableReason(entry, context) !== null) {
      return null
    }
    return {
      label: t(entry.label(context)),
      icon: entry.icon(context),
      destructive: entry.destructive === true,
      action: swipe.action,
      leaves: swipe.action.kind !== 'flag',
    }
  }
  // A finger moving left uncovers the right edge: the left swipe's action.
  const left = sideOf(swipe_left)
  const right = sideOf(swipe_right)
  const [offset, setOffset] = useState(0)
  const [dragging, setDragging] = useState(false)
  const [armed, setArmed] = useState(false)
  const gesture = useRef<Gesture | null>(null)
  // When the last swipe ended: the pointer click right after it is swallowed, so a swipe opens nothing.
  const swipedAt = useRef<number | null>(null)

  const commits = (dx: number, width: number, speed: number): boolean =>
    Math.abs(dx) >= width * SWIPE_SHARE || (Math.abs(dx) >= FLING_MIN && Math.abs(speed) >= FLING_SPEED && Math.sign(speed) === Math.sign(dx))

  function onPointerDown(event: PointerEvent<HTMLDivElement>): void {
    swipedAt.current = null
    if (event.pointerType !== 'touch' || gesture.current !== null || triage.busy || (left === null && right === null)) {
      return
    }
    gesture.current = {
      pointer: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      width: event.currentTarget.getBoundingClientRect().width,
      sideways: null,
      lastX: event.clientX,
      lastAt: event.timeStamp,
      speed: 0,
      dx: 0,
    }
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>): void {
    const held = gesture.current
    if (held?.pointer !== event.pointerId) {
      return
    }
    const dx = event.clientX - held.x
    const dy = event.clientY - held.y
    if (held.sideways === null) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < SLOP) {
        return
      }
      held.sideways = Math.abs(dx) > Math.abs(dy)
      if (!held.sideways) {
        // Scrolling: the browser has it.
        gesture.current = null
        return
      }
      event.currentTarget.setPointerCapture(event.pointerId)
      setDragging(true)
    }
    const elapsed = event.timeStamp - held.lastAt
    if (elapsed > 0) {
      held.speed = (event.clientX - held.lastX) / elapsed
    }
    held.lastX = event.clientX
    held.lastAt = event.timeStamp
    // Only towards a side that acts.
    const shown = dx < 0 ? (left === null ? 0 : dx) : right === null ? 0 : dx
    held.dx = shown
    setOffset(shown)
    setArmed(shown !== 0 && commits(shown, held.width, held.speed))
  }

  function end(event: PointerEvent<HTMLDivElement>, cancelled: boolean): void {
    const held = gesture.current
    if (held?.pointer !== event.pointerId) {
      return
    }
    gesture.current = null
    if (held.sideways !== true) {
      return
    }
    swipedAt.current = event.timeStamp
    setDragging(false)
    const speed = event.timeStamp - held.lastAt > STILL_MS ? 0 : held.speed
    const { dx } = held
    const swipe = dx < 0 ? left : dx > 0 ? right : null
    if (cancelled || swipe === null || !commits(dx, held.width, speed)) {
      setArmed(false)
      setOffset(0)
      return
    }
    if (swipe.action === 'menu') {
      setArmed(false)
      setOffset(0)
      setRevealed(true)
      return
    }
    if (!swipe.leaves) {
      setArmed(false)
      setOffset(0)
      void triage.run(swipe.action, [message])
      return
    }
    // Away off the row's side, its action still in its colour, until the move ends: then the row
    // is gone, or comes back where it failed.
    setOffset(Math.sign(dx) * held.width)
    void triage.run(swipe.action, [message]).finally(() => {
      setArmed(false)
      setOffset(0)
    })
  }

  const shown = offset < 0 ? left : offset > 0 ? right : null
  const Icon = shown?.icon
  return (
    <div className={cn('relative w-full', offset !== 0 && 'overflow-hidden')}>
      {shown !== null && Icon !== undefined && (
        <div
          aria-hidden="true"
          className={cn(
            'absolute inset-0 flex items-center gap-2 px-5 text-sm font-medium',
            offset < 0 ? 'justify-end' : 'justify-start',
            !armed
              ? 'bg-muted text-muted-foreground'
              : shown.destructive
                ? 'bg-destructive text-destructive-foreground'
                : 'bg-primary text-primary-foreground',
          )}
        >
          <Icon className="size-4 shrink-0" />
          {shown.label}
        </div>
      )}
      {/* The row's own controls (link, checkbox, toggles) stay the way to act without a swipe. */}
      <div
        className={cn(
          'relative flex w-full items-start',
          (left !== null || right !== null) && 'touch-pan-y touch-pinch-zoom',
          offset !== 0 && 'bg-background',
          !dragging && 'transition-transform duration-200 motion-reduce:transition-none',
        )}
        style={offset === 0 ? undefined : { transform: `translateX(${String(offset)}px)` }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={(event) => {
          end(event, false)
        }}
        onPointerCancel={(event) => {
          end(event, true)
        }}
        onClickCapture={(event) => {
          // A keyboard or assistive technology click (`detail` 0) is never a swipe's.
          const at = swipedAt.current
          if (at !== null && event.detail !== 0 && event.timeStamp - at < SWIPE_CLICK_MS) {
            swipedAt.current = null
            event.preventDefault()
            event.stopPropagation()
          }
        }}
      >
        {children}
        <RowActions message={message} items={items} context={context} compact={compact} revealed={revealed} onClose={closeActions} />
      </div>
    </div>
  )
}
