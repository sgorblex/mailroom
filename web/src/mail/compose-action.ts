import { useEffect, useRef, type RefObject } from 'react'

import type { AccountId } from '@/api/accounts.ts'

import { mailAction, unavailableReason, type ActionContext, type MailAction } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { NO_TRIAGE_ACTIONS } from './triage-actions.ts'

const COMPOSE = mailAction('compose')

/** Compose on the account shown, as `c` does, for a button starting a message. */
export interface ComposeAction {
  action: MailAction
  context: ActionContext
  /** Why it cannot start one now (a key of the mail namespace), `null` when it can. */
  reason: string | null
  /** Starts the message, when it can. */
  run: () => void
  /** The button: focus comes back to it once the composer closes, as the one that had it is gone. */
  button: RefObject<HTMLButtonElement | null>
}

/** Compose for a button of `accountId`'s: its context, why not, its run, the ref focus returns to. */
export function useComposeAction(accountId: AccountId): ComposeAction {
  const compose = useCompose()
  const button = useRef<HTMLButtonElement>(null)
  const context: ActionContext = {
    actions: NO_TRIAGE_ACTIONS,
    message: null,
    account: accountId,
    compose,
    openPicker: () => undefined,
  }
  const reason = unavailableReason(COMPOSE, context)
  const open = compose.session !== null
  const wasOpen = useRef(open)
  useEffect(() => {
    if (wasOpen.current && !open) {
      button.current?.focus()
    }
    wasOpen.current = open
  }, [open])
  return {
    action: COMPOSE,
    context,
    reason,
    run: () => {
      if (reason === null) {
        COMPOSE.run(context)
      }
    },
    button,
  }
}
