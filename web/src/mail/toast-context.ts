import { createContext, useContext, type ReactNode } from 'react'

/** A short message at the foot of the mail screen; `error` is announced assertively. */
export interface Toast {
  message: ReactNode
  tone?: 'info' | 'error'
  /** A button beside the message (Undo); choosing it closes the toast. */
  action?: { label: string; run: () => void }
}

export interface Toaster {
  /** Shows `toast` in place of the one showing. */
  show: (toast: Toast) => void
  /** Closes the toast showing, if any. */
  clear: () => void
}

export const ToastContext = createContext<Toaster | null>(null)

export function useToast(): Toaster {
  const toaster = useContext(ToastContext)
  if (toaster === null) {
    throw new Error('useToast outside a ToastHost')
  }
  return toaster
}
