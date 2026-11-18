import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactElement } from 'react'
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
}

/**
 * A menu of actions under `anchor` (the ARIA menu pattern): the first item it can take gets focus,
 * ↑ and ↓ move, Enter or a click chooses, Escape, Tab or a click outside closes it, and focus
 * goes back to `anchor`. Mounted while open.
 */
export function Menu({
  label,
  anchor,
  items,
  onClose,
}: {
  label: string
  anchor: HTMLElement
  items: MenuItem[]
  onClose: () => void
}): ReactElement {
  const menu = useRef<HTMLDivElement>(null)
  // Placed once, under the anchor as it was when the menu opened: scrolling or resizing closes it.
  // Below the anchor, or above it in the lower part of the window, where it would not fit.
  const [place] = useState(() => {
    const rect = anchor.getBoundingClientRect()
    const right = Math.max(8, window.innerWidth - rect.right)
    return rect.bottom > window.innerHeight * 0.6
      ? { bottom: window.innerHeight - rect.top + 4, right }
      : { top: rect.bottom + 4, right }
  })

  useLayoutEffect(() => {
    const first = items.findIndex((item) => item.disabledReason == null)
    menu.current?.querySelectorAll<HTMLElement>(ITEMS)[Math.max(first, 0)]?.focus()
    // Focused once, when it opens.
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
      style={place}
      className="fixed z-50 flex min-w-48 max-w-72 flex-col rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
    >
      {items.map((item) => {
        const disabled = item.disabledReason != null
        return (
          <button
            key={item.label}
            type="button"
            role={item.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
            tabIndex={-1}
            aria-checked={item.checked}
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
            <span className="flex items-center gap-2">
              {item.label}
              {item.checked === true && <Check aria-hidden="true" className="size-3.5" />}
            </span>
            {disabled && <span className="text-xs">{item.disabledReason}</span>}
          </button>
        )
      })}
    </div>,
    document.body,
  )
}
