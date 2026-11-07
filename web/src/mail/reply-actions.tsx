import { useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Forward, Reply, ReplyAll, type LucideIcon } from 'lucide-react'

import type { PrefillMode } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'

import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'

const ACTIONS: { mode: PrefillMode; label: string; shortcut: string; icon: LucideIcon }[] = [
  { mode: 'reply', label: 'compose.reply', shortcut: 'r', icon: Reply },
  { mode: 'reply_all', label: 'compose.replyAll', shortcut: 'a', icon: ReplyAll },
  { mode: 'forward', label: 'compose.forward', shortcut: 'f', icon: Forward },
]

/** Reply, reply all and forward of the open message; each opens the composer with its fields. */
export function ReplyActions({ account, messageRef }: { account: string; messageRef: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { start, canCompose } = useCompose()
  const hintId = useId()
  const allowed = canCompose(account)
  return (
    <div className="flex flex-col gap-1">
      <div role="group" aria-label={t('compose.replyActions')} className="flex flex-wrap gap-1">
        {ACTIONS.map(({ mode, label, shortcut, icon: Icon }) => (
          <Button
            key={mode}
            type="button"
            variant="outline"
            size="sm"
            title={`${t(label)} (${shortcut})`}
            aria-keyshortcuts={shortcut}
            aria-disabled={!allowed}
            aria-describedby={allowed ? undefined : hintId}
            onClick={() => {
              if (allowed) {
                start({ kind: mode, account, ref: messageRef })
              }
            }}
          >
            <Icon aria-hidden="true" />
            {t(label)}
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
