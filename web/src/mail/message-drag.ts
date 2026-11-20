import { useSyncExternalStore } from 'react'

import type { Target } from './triage-context.ts'

/** What a dragged row carries: the refs of the messages it moves. */
export const MESSAGES_TYPE = 'application/x-mailroom-messages'

// The messages being dragged from this page: a drop target reads them while the drag is over it,
// when the browser keeps the carried data from it.
let dragged: Target[] | null = null
const listeners = new Set<() => void>()

function set(value: Target[] | null): void {
  dragged = value
  for (const listener of listeners) {
    listener()
  }
}

/** Starts dragging `targets`; with more than one, the drag shows `label` instead of the row. */
export function startMessageDrag(transfer: DataTransfer, targets: Target[], label: string): void {
  transfer.setData(MESSAGES_TYPE, JSON.stringify(targets.map((target) => target.ref)))
  transfer.effectAllowed = 'move'
  if (targets.length > 1 && typeof transfer.setDragImage === 'function') {
    const image = document.createElement('div')
    image.textContent = label
    image.className = 'fixed -top-24 left-0 rounded-md border bg-background px-2 py-1 text-sm text-foreground'
    document.body.append(image)
    transfer.setDragImage(image, 0, 0)
    // The browser has taken its picture by the next task.
    setTimeout(() => {
      image.remove()
    }, 0)
  }
  set(targets)
}

export function endMessageDrag(): void {
  set(null)
}

/** Whether a drag carries messages dragged from this page. */
export function carriesMessages(transfer: DataTransfer): boolean {
  return dragged !== null && [...transfer.types].includes(MESSAGES_TYPE)
}

/** The messages being dragged from this page, `null` when none is. */
export function useDraggedMessages(): Target[] | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    () => dragged,
  )
}
