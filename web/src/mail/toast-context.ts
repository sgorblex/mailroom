import { createContext, useContext, type ReactNode } from 'react'

/** A short message at the foot of the mail screen; `error` is announced assertively. */
export interface Toast {
  message: ReactNode
  tone?: 'info' | 'error'
  /** A button beside the message (Undo); choosing it closes the toast. */
  action?: { label: string; run: () => void }
}

/**
 * A lasting toast (a send held for an Undo): larger, with no timer, shown until its owner clears
 * it. `message` may change as it shows (a countdown), so screen readers hear `spoken` instead, once
 * for each new text; `null` says nothing.
 */
export interface PinnedToast {
  message: ReactNode
  spoken: string | null
  tone?: 'info' | 'error'
  /** A button beside the message; its owner decides what it leaves. `busy` while it runs. */
  action?: { label: string; run: () => void; busy?: boolean }
  /** Shows a close button that calls this. */
  onDismiss?: () => void
}

export interface Toaster {
  /** Shows `toast` in place of the one showing. */
  show: (toast: Toast) => void
  /** Closes the toast showing, if any. */
  clear: () => void
  /** Shows or updates the pinned toast under `key`, beside the others; `null` takes it away. */
  pin: (key: string, toast: PinnedToast | null) => void
}

export const ToastContext = createContext<Toaster | null>(null)

export function useToast(): Toaster {
  const toaster = useContext(ToastContext)
  if (toaster === null) {
    throw new Error('useToast outside a ToastHost')
  }
  return toaster
}
