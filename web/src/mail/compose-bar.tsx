import { createElement, useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { FileUp, PenLine } from 'lucide-react'

import type { AccountId } from '@/api/accounts.ts'
import { importDraft, type ImportSource } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'

import { useComposeAction } from './compose-action.ts'
import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { ImportDialog } from './import-dialog.tsx'
import { ScheduledNotice, SentNotice } from './send-status.tsx'

/**
 * Starts a message on the account shown, or imports one built elsewhere, or goes back to the draft
 * a message is in front of, with the notices of what was sent or scheduled. `starts` narrows the
 * buttons starting one: `import` alone on a narrow screen, whose header has Compose, and `none`
 * there while a message takes the screen.
 */
export function ComposeBar({
  accountId,
  starts = 'all',
}: {
  accountId: AccountId
  starts?: 'all' | 'import' | 'none'
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const compose = useCompose()
  const { session, shown, backToDraft } = compose
  const hintId = useId()
  const { action, context, reason, run, button } = useComposeAction(accountId)
  const allowed = reason === null
  const open = session !== null
  const [importing, setImporting] = useState<string | null>(null)
  const withCompose = starts === 'all'

  /** A new draft of the account shown, under `composeId`, opened in the composer once saved. */
  function imported(composeId: string, ref: string | null): void {
    setImporting(null)
    compose.start(ref === null ? { kind: 'recover', account: accountId, composeId } : { kind: 'draft', ref })
  }

  const importAs = (composeId: string) => (source: ImportSource) =>
    importDraft(
      { account: accountId, compose_id: composeId, ref: null, from: null, to: null, cc: null, bcc: null, subject: null },
      source,
    )

  return (
    <div className="flex flex-col gap-2">
      {!open && starts !== 'none' && (
        <div className="flex flex-wrap gap-2">
          {withCompose && (
            <Button
              ref={button}
              type="button"
              size="sm"
              className="self-start"
              title={`${t(action.label(context))} (${action.shortcut ?? ''})`}
              aria-keyshortcuts={action.shortcut}
              aria-disabled={!allowed}
              aria-describedby={allowed ? undefined : hintId}
              onClick={run}
            >
              {createElement(action.icon(context), { 'aria-hidden': true })}
              {t(action.label(context))}
            </Button>
          )}
          {allowed && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setImporting(crypto.randomUUID())
              }}
            >
              <FileUp aria-hidden="true" />
              {t('designed.import')}
            </Button>
          )}
        </div>
      )}
      {importing !== null && (
        <ImportDialog
          replacing={false}
          onImport={importAs(importing)}
          onDone={(draft) => {
            imported(importing, draft.ref)
          }}
          onCancel={() => {
            setImporting(null)
          }}
        />
      )}
      {open && !shown && (
        <Button type="button" variant="outline" size="sm" className="self-start" onClick={backToDraft}>
          <PenLine aria-hidden="true" />
          {t('compose.backToDraft')}
        </Button>
      )}
      {reason !== null && !open && withCompose && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {t(reason)}
        </p>
      )}
      <SentNotice />
      <ScheduledNotice />
    </div>
  )
}
