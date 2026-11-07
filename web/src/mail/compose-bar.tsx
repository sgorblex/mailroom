import { useEffect, useId, useRef, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { PenLine } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'

import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { SentNotice } from './send-status.tsx'

/** Starts a message on the account shown, or goes back to the draft a message is in front of. */
export function ComposeBar({ accountId }: { accountId: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { session, shown, start, backToDraft, canCompose } = useCompose()
  const hintId = useId()
  const button = useRef<HTMLButtonElement>(null)
  const allowed = canCompose(accountId)
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
          title={`${t('compose.compose')} (c)`}
          aria-keyshortcuts="c"
          aria-disabled={!allowed}
          aria-describedby={allowed ? undefined : hintId}
          onClick={() => {
            if (allowed) {
              start({ kind: 'new', account: accountId })
            }
          }}
        >
          <PenLine aria-hidden="true" />
          {t('compose.compose')}
        </Button>
      )}
      {open && !shown && (
        <Button type="button" variant="outline" size="sm" className="self-start" onClick={backToDraft}>
          <PenLine aria-hidden="true" />
          {t('compose.backToDraft')}
        </Button>
      )}
      {!allowed && !open && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {t('compose.offHint')}
        </p>
      )}
      <SentNotice />
    </div>
  )
}
