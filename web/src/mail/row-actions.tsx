import { useEffect, useRef, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Bookmark, Star } from 'lucide-react'

import { useAccountHandled } from '@/accounts/accounts-context.ts'
import type { MessageItem } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { unavailableReason, type ActionContext, type MailAction } from './action-registry.ts'
import { MAIL_NS } from './i18n.ts'
import { useTriage } from './triage-context.ts'

/**
 * The actions the user chose for message rows, on this message alone, whatever is selected or open.
 * Over the row's end, taking no width, while the row is hovered or has focus. On a touch screen,
 * only once a swipe shows them (`revealed`), until a tap elsewhere, Escape or an action; always
 * there for screen readers. Siblings of the row's link, so a click opens nothing.
 */
export function RowActions({
  message,
  items,
  context,
  compact,
  revealed,
  onClose,
}: {
  message: MessageItem
  items: readonly MailAction[]
  context: ActionContext
  compact: boolean
  revealed: boolean
  onClose: () => void
}): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const { busy, flagsOf } = useTriage()
  const handledIsStar = useAccountHandled()(message.account_id).kind === 'star'
  const flags = flagsOf(message)
  const bar = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!revealed) {
      return undefined
    }
    const onPointerDown = (event: PointerEvent): void => {
      if (!(event.target instanceof Node) || bar.current?.contains(event.target) !== true) {
        onClose()
      }
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [revealed, onClose])
  if (items.length === 0) {
    return null
  }
  return (
    <span
      ref={bar}
      data-row-actions
      className={cn(
        'absolute right-2 flex items-center gap-0.5 rounded-md border bg-background shadow-xs',
        compact ? 'top-1' : 'top-2',
        !revealed && [
          // Hidden, it takes no click: a tap on the date opens the message.
          'pointer-events-none opacity-0 group-focus-within/row:pointer-events-auto group-focus-within/row:opacity-100',
          'group-hover/row:pointer-events-auto group-hover/row:opacity-100',
          // Still there for screen readers.
          'pointer-coarse:sr-only',
        ],
      )}
    >
      {items.map((action) => {
        const label = t(action.label(context))
        const reason = unavailableReason(action, context)
        const disabled = reason !== null || (action.group === 'triage' && busy)
        // A toggle keeps one name; its state is aria-pressed. The tooltip says what a click does.
        const toggle =
          action.id === 'star'
            ? { name: t('triage.star'), pressed: flags.flagged, icon: Star, set: 'fill-current text-star' }
            : action.id === 'handled'
              ? { name: t('list.handled'), pressed: flags.handled, icon: Bookmark, set: 'fill-current text-primary' }
              : null
        const title = action.id === 'star' && handledIsStar ? t('triage.starIsHandled', { action: label }) : label
        const Icon = toggle?.icon ?? action.icon(context)
        return (
          <Button
            key={action.id}
            variant="ghost"
            size="icon-xs"
            aria-label={toggle?.name ?? label}
            title={reason === null ? title : `${label}: ${t(reason)}`}
            aria-pressed={toggle?.pressed}
            aria-disabled={disabled}
            className={cn(
              'shrink-0 text-muted-foreground hover:text-foreground',
              'aria-disabled:cursor-not-allowed aria-disabled:text-muted-foreground/50 aria-disabled:hover:text-muted-foreground/50',
              revealed && 'pointer-coarse:size-9',
            )}
            onClick={() => {
              if (!disabled) {
                onClose()
                action.run(context)
              }
            }}
          >
            <Icon aria-hidden="true" className={cn('size-3.5', toggle?.pressed === true && toggle.set)} />
          </Button>
        )
      })}
    </span>
  )
}
