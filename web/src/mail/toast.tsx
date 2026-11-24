import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { ToastContext, type PinnedToast, type Toast, type Toaster } from './toast-context.ts'

/** How long a toast shows, not counting the time the pointer or focus is on it. */
export const TOAST_MS = 8000

/** The message list's id: focus goes there when a toast closes and what had it before is gone. */
export const TOAST_RETURN_ID = 'message-list'

const STATUS_ID = 'toast-status'
const ALERT_ID = 'toast-alert'

/** The live regions of the pinned toasts: each one's `spoken`, a line apiece, so a new one is heard alone. */
export const PINNED_STATUS_ID = 'toast-pinned-status'
export const PINNED_ALERT_ID = 'toast-pinned-alert'

const spokenId = (key: string): string => `toast-pinned-${key}`

/**
 * Holds one toast at a time over `children` (mounted above what changes on navigation, so a toast
 * outlives the list it was shown on), and the pinned ones below it. A toast closes after
 * `TOAST_MS`, the clock paused while the pointer is on it or focus is in it; a pinned one stays
 * until its owner takes it away. Their text is announced from live regions mounted for as long as
 * the host is, the assertive ones for an error.
 */
export function ToastHost({ children }: { children: ReactNode }): ReactElement {
  const [toast, setToast] = useState<(Toast & { id: number }) | null>(null)
  const [pinned, setPinned] = useState<{ key: string; toast: PinnedToast }[]>([])
  const next = useRef(0)
  const show = useCallback((value: Toast) => {
    next.current += 1
    setToast({ ...value, id: next.current })
  }, [])
  const close = useCallback(() => {
    setToast(null)
  }, [])
  const pin = useCallback((key: string, value: PinnedToast | null) => {
    setPinned((list) => {
      if (value === null) {
        return list.filter((each) => each.key !== key)
      }
      return list.some((each) => each.key === key)
        ? list.map((each) => (each.key === key ? { key, toast: value } : each))
        : [...list, { key, toast: value }]
    })
  }, [])
  const toaster = useMemo<Toaster>(() => ({ show, clear: close, pin }), [show, close, pin])
  const error = toast?.tone === 'error'
  const spoken = (tone: 'error' | 'info'): ReactElement[] =>
    pinned
      .filter((each) => each.toast.spoken !== null && (each.toast.tone === 'error') === (tone === 'error'))
      .map((each) => (
        <div key={each.key} id={spokenId(each.key)}>
          {each.toast.spoken}
        </div>
      ))
  return (
    <ToastContext value={toaster}>
      <div id={STATUS_ID} role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {toast !== null && !error && toast.message}
      </div>
      <div id={ALERT_ID} role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {toast !== null && error && toast.message}
      </div>
      <div id={PINNED_STATUS_ID} role="status" aria-live="polite" aria-atomic="false" className="sr-only">
        {spoken('info')}
      </div>
      <div id={PINNED_ALERT_ID} role="alert" aria-live="assertive" aria-atomic="false" className="sr-only">
        {spoken('error')}
      </div>
      {children}
      {(toast !== null || pinned.length > 0) && (
        <div className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--tab-bar)+var(--action-bar,0px)+1rem)] z-50 flex flex-col items-center gap-2 px-4">
          {toast !== null && <ToastCard key={toast.id} toast={toast} onClose={close} />}
          {pinned.map((each) => (
            <PinnedCard key={each.key} id={each.key} toast={each.toast} />
          ))}
        </div>
      )}
    </ToastContext>
  )
}

/** Puts focus back on `back` when it is still there, else on the message list, rather than on the page. */
function focusBack(back: Element | null): void {
  if (back?.isConnected === true) {
    if (back instanceof HTMLElement) {
      back.focus()
    }
  } else {
    // The list's first row in the page, which its virtualised scroll may hold above the view: not scrolled to.
    document.getElementById(TOAST_RETURN_ID)?.querySelector('a')?.focus({ preventScroll: true })
  }
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

  function closeFromInside(): void {
    focusBack(returnTo.current)
    onClose()
  }

  const describedBy = toast.tone === 'error' ? ALERT_ID : STATUS_ID
  return (
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
  )
}

/**
 * Puts focus back with `focusBack` when what `node` holds goes away with focus in it, rather than
 * leaving focus on the page. Run as a layout effect's cleanup, so the node is still in the page.
 */
function useFocusBackOnRemoval(node: RefObject<HTMLElement | null>, returnTo: RefObject<Element | null>): void {
  useLayoutEffect(() => {
    const held = node.current
    return () => {
      if (held?.contains(document.activeElement) === true) {
        // What had focus last, not when the node came: the toast may have been focused since.
        focusBackFrom(returnTo)
      }
    }
  }, [node, returnTo])
}

function focusBackFrom(returnTo: RefObject<Element | null>): void {
  focusBack(returnTo.current)
}

/** A pinned toast: wider and taller than the others, its buttons described by what it last said. */
function PinnedCard({ id, toast }: { id: string; toast: PinnedToast }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const card = useRef<HTMLDivElement>(null)
  // What had focus before the toast took it, where focus goes back when the toast or its action goes.
  const returnTo = useRef<Element | null>(null)
  useFocusBackOnRemoval(card, returnTo)
  const describedBy = toast.spoken === null ? undefined : spokenId(id)
  return (
    <div
      ref={card}
      className={cn(
        'pointer-events-auto flex w-full max-w-lg items-center gap-3 rounded-xl border bg-background p-3 pl-4 text-base shadow-xl',
        toast.tone === 'error' && 'border-destructive/40',
      )}
      onFocus={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          returnTo.current = event.relatedTarget
        }
      }}
    >
      <p aria-hidden="true" className="min-w-0 flex-1 break-words">
        {toast.message}
      </p>
      {toast.action !== undefined && <PinnedActionButton action={toast.action} describedBy={describedBy} returnTo={returnTo} />}
      {toast.onDismiss !== undefined && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('toast.dismiss')}
          aria-describedby={describedBy}
          onClick={() => {
            focusBack(returnTo.current)
            toast.onDismiss?.()
          }}
        >
          <X aria-hidden="true" />
        </Button>
      )}
    </div>
  )
}

/** A pinned toast's action, which hands focus back when it goes (an Undo whose time ran out) while it has it. */
function PinnedActionButton({
  action,
  describedBy,
  returnTo,
}: {
  action: NonNullable<PinnedToast['action']>
  describedBy: string | undefined
  returnTo: RefObject<Element | null>
}): ReactElement {
  const button = useRef<HTMLButtonElement>(null)
  useFocusBackOnRemoval(button, returnTo)
  return (
    <Button
      ref={button}
      variant="outline"
      aria-describedby={describedBy}
      aria-disabled={action.busy === true}
      onClick={() => {
        if (action.busy !== true) {
          action.run()
        }
      }}
    >
      {action.label}
    </Button>
  )
}
