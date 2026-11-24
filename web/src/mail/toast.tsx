import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { ToastContext, type Toast, type Toaster } from './toast-context.ts'

/** How long a toast shows, not counting the time the pointer or focus is on it. */
export const TOAST_MS = 8000

/** The message list's id: focus goes there when a toast closes and what had it before is gone. */
export const TOAST_RETURN_ID = 'message-list'

const STATUS_ID = 'toast-status'
const ALERT_ID = 'toast-alert'

/**
 * Holds one toast at a time over `children` (mounted above what changes on navigation, so a toast
 * outlives the list it was shown on). It closes after `TOAST_MS`, the clock paused while the
 * pointer is on it or focus is in it. Its text is announced from live regions mounted for as long
 * as the host is, the assertive one for an error.
 */
export function ToastHost({ children }: { children: ReactNode }): ReactElement {
  const [toast, setToast] = useState<(Toast & { id: number }) | null>(null)
  const next = useRef(0)
  const show = useCallback((value: Toast) => {
    next.current += 1
    setToast({ ...value, id: next.current })
  }, [])
  const close = useCallback(() => {
    setToast(null)
  }, [])
  const toaster = useMemo<Toaster>(() => ({ show, clear: close }), [show, close])
  const error = toast?.tone === 'error'
  return (
    <ToastContext value={toaster}>
      <div id={STATUS_ID} role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {toast !== null && !error && toast.message}
      </div>
      <div id={ALERT_ID} role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {toast !== null && error && toast.message}
      </div>
      {children}
      {toast !== null && <ToastCard key={toast.id} toast={toast} onClose={close} />}
    </ToastContext>
  )
}

function ToastCard({ toast, onClose }: { toast: Toast; onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const paused = hovered || focused
  const remaining = useRef(TOAST_MS)
  // What had focus before the toast took it.
  const returnTo = useRef<Element | null>(null)

  useEffect(() => {
    if (paused) {
      return
    }
    const started = Date.now()
    const timer = setTimeout(onClose, remaining.current)
    return () => {
      clearTimeout(timer)
      remaining.current = Math.max(0, remaining.current - (Date.now() - started))
    }
  }, [paused, onClose])

  /** Closes it, putting focus back where it was (else on the message list) rather than on the page. */
  function closeFromInside(): void {
    const back = returnTo.current
    if (back?.isConnected === true) {
      if (back instanceof HTMLElement) {
        back.focus()
      }
    } else {
      // The list's first row in the page, which its virtualised scroll may hold above the view: not scrolled to.
      document.getElementById(TOAST_RETURN_ID)?.querySelector('a')?.focus({ preventScroll: true })
    }
    onClose()
  }

  const describedBy = toast.tone === 'error' ? ALERT_ID : STATUS_ID
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--tab-bar)+1rem)] z-50 flex justify-center px-4">
      <div
        className={cn(
          'pointer-events-auto flex max-w-md items-center gap-2 rounded-lg border bg-background p-2 pl-3 text-sm shadow-lg',
          toast.tone === 'error' && 'border-destructive/40',
        )}
        onMouseEnter={() => {
          setHovered(true)
        }}
        onMouseLeave={() => {
          setHovered(false)
        }}
        onFocus={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) {
            returnTo.current = event.relatedTarget
          }
          setFocused(true)
        }}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) {
            setFocused(false)
          }
        }}
      >
        {/* Screen readers hear the text from the live region, which describes the buttons. */}
        <div aria-hidden="true" className="min-w-0 flex-1">
          {toast.message}
        </div>
        {toast.action !== undefined && (
          <Button
            variant="outline"
            size="sm"
            aria-describedby={describedBy}
            onClick={() => {
              closeFromInside()
              toast.action?.run()
            }}
          >
            {toast.action.label}
          </Button>
        )}
        <Button variant="ghost" size="icon-xs" aria-label={t('toast.dismiss')} aria-describedby={describedBy} onClick={closeFromInside}>
          <X aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}
