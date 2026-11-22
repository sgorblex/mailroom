import { createElement, useEffect, useId, useRef, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { PenLine } from 'lucide-react'

import type { AccountId } from '@/api/accounts.ts'
import { Button } from '@/components/ui/button.tsx'

import { mailAction, unavailableReason, type ActionContext } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { HeldNotices, SentNotice } from './send-status.tsx'
import { NO_TRIAGE_ACTIONS } from './triage-actions.ts'

const COMPOSE = mailAction('compose')

/** Starts a message on the account shown, or goes back to the draft a message is in front of. */
export function ComposeBar({ accountId }: { accountId: AccountId }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const compose = useCompose()
  const { session, shown, backToDraft } = compose
  const hintId = useId()
  const button = useRef<HTMLButtonElement>(null)
  const context: ActionContext = {
    actions: NO_TRIAGE_ACTIONS,
    message: null,
    account: accountId,
    compose,
    openPicker: () => undefined,
  }
  const reason = unavailableReason(COMPOSE, context)
  const allowed = reason === null
  const open = session !== null

  // The composer that had focus is gone.
  const wasOpen = useRef(open)
  useEffect(() => {
    if (wasOpen.current && !open) {
      button.current?.focus()
    }
    wasOpen.current = open
  }, [open])

  return (
    <div className="flex flex-col gap-2">
      {!open && (
        <Button
          ref={button}
          type="button"
          size="sm"
          className="self-start"
          title={`${t(COMPOSE.label(context))} (${COMPOSE.shortcut ?? ''})`}
          aria-keyshortcuts={COMPOSE.shortcut}
          aria-disabled={!allowed}
          aria-describedby={allowed ? undefined : hintId}
          onClick={() => {
            if (allowed) {
              COMPOSE.run(context)
            }
          }}
        >
          {createElement(COMPOSE.icon(context), { 'aria-hidden': true })}
          {t(COMPOSE.label(context))}
        </Button>
      )}
      {open && !shown && (
        <Button type="button" variant="outline" size="sm" className="self-start" onClick={backToDraft}>
          <PenLine aria-hidden="true" />
          {t('compose.backToDraft')}
        </Button>
      )}
      {reason !== null && !open && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {t(reason)}
        </p>
      )}
      <HeldNotices />
      <SentNotice />
    </div>
  )
}
