import { createElement, useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { AccountId } from '@/api/accounts.ts'
import { Button } from '@/components/ui/button.tsx'

import { listedActions, unavailableReason, type ActionContext } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { useDraftFilter } from './filter-draft.ts'
import { MAIL_NS } from './i18n.ts'
import { NO_TRIAGE_ACTIONS } from './triage-actions.ts'

/**
 * Reply, reply all and forward of the open message, each opening the composer with its fields;
 * then print, download and a filter drafted from it, which the composer's state does not hold back.
 */
export function ReplyActions({ account, messageRef }: { account: AccountId; messageRef: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const compose = useCompose()
  const draftFilter = useDraftFilter()
  const hintId = useId()
  const context: ActionContext = {
    actions: NO_TRIAGE_ACTIONS,
    message: { ref: messageRef, account_id: account },
    account,
    compose,
    openPicker: () => undefined,
    draftFilter,
  }
  const actions = listedActions('reply', context)
  const allowed = actions.every((action) => unavailableReason(action, context) === null)
  const forms = listedActions('message', context)
  return (
    <div className="flex flex-col gap-1">
      <div role="group" aria-label={t('compose.replyActions')} className="flex flex-wrap gap-1">
        {actions.map((action) => (
          <Button
            key={action.id}
            type="button"
            variant="outline"
            size="sm"
            title={`${t(action.label(context))} (${action.shortcut ?? ''})`}
            aria-keyshortcuts={action.shortcut}
            aria-disabled={!allowed}
            aria-describedby={allowed ? undefined : hintId}
            onClick={() => {
              if (allowed) {
                action.run(context)
              }
            }}
          >
            {createElement(action.icon(context), { 'aria-hidden': true })}
            {t(action.label(context))}
          </Button>
        ))}
        {forms.map((action) => (
          <Button
            key={action.id}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              action.run(context)
            }}
          >
            {createElement(action.icon(context), { 'aria-hidden': true })}
            {t(action.label(context))}
          </Button>
        ))}
      </div>
      {!allowed && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {t('compose.offHint')}
        </p>
      )}
    </div>
  )
}
