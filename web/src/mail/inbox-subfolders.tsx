import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { ListTree } from 'lucide-react'

import { useInboxSubfolders } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'

import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'
import { useToast } from './toast-context.ts'

/** Whether *All inboxes* takes in INBOX's subfolders, kept for the user on every device. */
export function InboxSubfoldersButton(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { included, change } = useInboxSubfolders()
  const { show } = useToast()
  const [saving, setSaving] = useState(false)

  async function toggle(): Promise<void> {
    setSaving(true)
    try {
      await change(!included)
    } catch (failure) {
      show({ message: mailErrorMessage(t, failure, t('views.subfoldersFailed')), tone: 'error' })
    }
    setSaving(false)
  }

  return (
    <Button
      type="button"
      variant={included ? 'secondary' : 'outline'}
      size="xs"
      aria-pressed={included}
      aria-disabled={saving}
      onClick={() => {
        if (!saving) {
          void toggle()
        }
      }}
    >
      <ListTree aria-hidden="true" />
      {t('views.includeSubfolders')}
    </Button>
  )
}
