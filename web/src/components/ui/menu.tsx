import { useEffect, useLayoutEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactElement, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check } from 'lucide-react'

import { cn } from '@/lib/utils.ts'

const ITEMS = '[role="menuitem"], [role="menuitemcheckbox"]'

export interface MenuItem {
  label: string
  onSelect: () => void
  /** Why it cannot be chosen: shown under it, and the item is disabled. */
  disabledReason?: string | null
  destructive?: boolean
  /** A choice that is on or off: a check mark shows it on. */
  checked?: boolean
  /** The key that does the same outside the menu, shown beside it. */
  shortcut?: string
  /** Shown before the label, for sight only (an account's colour dot). */
  icon?: ReactNode
  /** Tells apart items whose labels may be the same (two accounts of one name); else the label does. */
  id?: string
}

/** Where a context menu was asked for, in viewport coordinates. */
export interface MenuPoint {
  x: number
  y: number
}

/** The place of a menu under `anchor`, or above it in the lower part of the window, where it would not fit. */
function underAnchor(anchor: HTMLElement): { top?: number; bottom?: number; right: number } {
  const rect = anchor.getBoundingClientRect()
  const right = Math.max(8, window.innerWidth - rect.right)
  return rect.bottom > window.innerHeight * 0.6
    ? { bottom: window.innerHeight - rect.top + 4, right }
    : { top: rect.bottom + 4, right }
}

/**
 * A menu of actions under `anchor` (the ARIA menu pattern), or at `point` for a context menu: the
 * first item it can take gets focus, ↑ and ↓ move, Enter or a click chooses, Escape, Tab, focus
 * leaving it or a click outside closes it, and focus goes back to `anchor`. Mounted while open;
 * scrolling or resizing closes it. A `heading` shows over the items, for sight only: `label` says it.
 */
export function Menu({
  label,
  heading,
  anchor,
  point,
  items,
  onClose,
}: {
  label: string
  heading?: string
  anchor: HTMLElement
  point?: MenuPoint
  items: MenuItem[]
  onClose: () => void
}): ReactElement {
  const menu = useRef<HTMLDivElement>(null)
  // Placed once, as the anchor or the point was when the menu opened.
  const [place] = useState(() => (point === undefined ? underAnchor(anchor) : { left: point.x, top: point.y }))

  useLayoutEffect(() => {
    const element = menu.current
    // At a point, kept inside the window: left of the point at its right edge, above it at the bottom.
    if (element !== null && point !== undefined) {
      const { width, height } = element.getBoundingClientRect()
      if (point.x + width > window.innerWidth - 8) {
        element.style.left = `${String(Math.max(8, point.x - width))}px`
      }
      if (point.y + height > window.innerHeight - 8) {
        element.style.top = `${String(Math.max(8, point.y - height))}px`
      }
    }
    const first = items.findIndex((item) => item.disabledReason == null)
    element?.querySelectorAll<HTMLElement>(ITEMS)[Math.max(first, 0)]?.focus()
    // Placed and focused once, when it opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const close = (event: Event): void => {
      if (event.target instanceof Node && (menu.current?.contains(event.target) === true || anchor.contains(event.target))) {
        return
      }
      onClose()
    }
    const leave = (): void => {
      onClose()
    }
    document.addEventListener('pointerdown', close)
    window.addEventListener('resize', leave)
    window.addEventListener('scroll', leave, true)
    return () => {
      document.removeEventListener('pointerdown', close)
      window.removeEventListener('resize', leave)
      window.removeEventListener('scroll', leave, true)
    }
  }, [anchor, onClose])

  function closeAndReturn(): void {
    onClose()
    anchor.focus()
  }

  function onBlur(event: FocusEvent<HTMLDivElement>): void {
    // Focus gone elsewhere on the page; not when it leaves the window.
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) {
      onClose()
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    const entries = [...(menu.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])]
    const at = entries.findIndex((entry) => entry === document.activeElement)
    const move = (index: number): void => {
      entries[(index + entries.length) % entries.length]?.focus()
    }
    switch (event.key) {
      case 'ArrowDown':
        move(at + 1)
        break
      case 'ArrowUp':
        move(at - 1)
        break
      case 'Home':
        move(0)
        break
      case 'End':
        move(entries.length - 1)
        break
      case 'Escape':
        closeAndReturn()
        break
      case 'Tab':
        onClose()
        return
      default:
        return
    }
    event.preventDefault()
    event.stopPropagation()
  }

  return createPortal(
    <div
      ref={menu}
      role="menu"
      aria-label={label}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      // A context-menu key released over an open menu: never the browser's menu on top of it.
      onContextMenu={(event) => {
        event.preventDefault()
      }}
      style={place}
      className="fixed z-50 flex min-w-48 max-w-72 flex-col rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
    >
      {heading !== undefined && (
        <p aria-hidden="true" className="mb-1 truncate border-b px-2 pt-1 pb-2 text-xs text-muted-foreground">
          {heading}
        </p>
      )}
      {items.map((item) => {
        const disabled = item.disabledReason != null
        return (
          <button
            key={item.id ?? item.label}
            type="button"
            role={item.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
            tabIndex={-1}
            aria-checked={item.checked}
            aria-keyshortcuts={disabled ? undefined : item.shortcut}
            aria-disabled={disabled || undefined}
            onClick={() => {
              if (!disabled) {
                // Back on the anchor first: a dialog the item opens returns focus there.
                closeAndReturn()
                item.onSelect()
              }
            }}
            className={cn(
              'flex flex-col items-start rounded-sm px-2 py-1.5 text-left text-sm outline-none focus-visible:bg-accent',
              disabled ? 'cursor-default text-muted-foreground' : 'hover:bg-accent',
              item.destructive === true && !disabled && 'text-destructive',
            )}
          >
            <span className="flex w-full items-center gap-2">
              {item.icon}
              {item.label}
              {item.checked === true && <Check aria-hidden="true" className="size-3.5" />}
              {item.shortcut !== undefined && (
                <kbd aria-hidden="true" className="ml-auto pl-4 font-sans text-xs text-muted-foreground">
                  {item.shortcut}
                </kbd>
              )}
            </span>
            {disabled && <span className="text-xs">{item.disabledReason}</span>}
          </button>
        )
      })}
    </div>,
    // Inside a modal dialog it opened from, as the page behind one is out of reach.
    anchor.closest('dialog') ?? document.body,
  )
}
