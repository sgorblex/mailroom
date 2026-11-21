import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { listAccounts, type Account } from '@/api/accounts.ts'
import { apiErrorMessage } from '@/api/http.ts'
import {
  readIndexStatus,
  readNotificationFolders,
  setNotificationFolders,
  type AccountNotifications,
  type IndexFolder,
} from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { notificationAccess, notifies, type NotificationAccess } from '@/mail/notification-choice.ts'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from '@/mail/messages.ts'
import { ACCOUNTS_KEY, indexStatusKey, NOTIFICATIONS_KEY, READ_ON_MOUNT } from '@/mail/queries.ts'
import { isSameFolder } from '@/mail/triage-context.ts'

/**
 * Desktop notifications: the browser's permission, asked only from its button here, and per
 * account the folders whose new mail notifies (INBOX until the user chooses).
 */
export function NotificationSettings(): ReactElement {
  const { t } = useTranslation()
  const [access, setAccess] = useState<NotificationAccess>(notificationAccess)
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), ...READ_ON_MOUNT })
  const chosen = useQuery({ queryKey: NOTIFICATIONS_KEY, queryFn: ({ signal }) => readNotificationFolders(signal), ...READ_ON_MOUNT })

  async function allow(): Promise<void> {
    setAccess(await Notification.requestPermission())
  }

  return (
    <section aria-labelledby="desktop-notifications" className="flex flex-col gap-3">
      <h2 id="desktop-notifications" className="font-medium">
        {t('desktopNotifications.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('desktopNotifications.intro')}</p>
      {access === 'default' ? (
        <Button type="button" onClick={() => void allow()}>
          {t('desktopNotifications.allow')}
        </Button>
      ) : (
        <p className="text-sm">
          {t(`desktopNotifications.${access}`)}
        </p>
      )}
      {(accounts.isError || chosen.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {apiErrorMessage(accounts.error ?? chosen.error, t('desktopNotifications.loadFailed'))}
        </p>
      )}
      {chosen.data !== undefined &&
        accounts.data?.map((account) => <AccountFolders key={account.id} account={account} chosen={chosen.data} />)}
    </section>
  )
}

/**
 * A folder whose new mail can notify: one on the index; on Gmail INBOX, read from All Mail,
 * whose own new mail is told as INBOX's.
 */
function notifying(folder: IndexFolder, gmail: boolean): boolean {
  return (
    folder.enabled &&
    (folder.indexed_as === 'folder' ? !(gmail && folder.role === 'all') : folder.indexed_as === 'label' && isSameFolder(folder.path, 'INBOX'))
  )
}

function AccountFolders({ account, chosen }: { account: Account; chosen: AccountNotifications[] }): ReactElement {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const status = useQuery({
    queryKey: indexStatusKey(account.id),
    queryFn: ({ signal }) => readIndexStatus(account.id, signal),
    ...READ_ON_MOUNT,
  })
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const current = chosen.find((each) => each.account_id === account.id)?.folders ?? ['INBOX']

  async function toggle(path: string, on: boolean): Promise<void> {
    setSaving(true)
    setFailure(null)
    const folders = on ? [...current, path] : current.filter((each) => !isSameFolder(each, path))
    try {
      const saved = await setNotificationFolders(account.id, folders)
      queryClient.setQueryData<AccountNotifications[]>(NOTIFICATIONS_KEY, (all) => [
        ...(all ?? []).filter((each) => each.account_id !== account.id),
        saved,
      ])
    } catch (error) {
      setFailure(mailErrorMessage(t, error, t('desktopNotifications.saveFailed')))
    } finally {
      setSaving(false)
    }
  }

  const headingId = `desktop-notifications-${String(account.id)}`
  const gmail = status.data?.gmail === true
  const folders = status.data?.folders.filter((folder) => notifying(folder, gmail))
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h3 id={headingId} className="text-sm font-medium">
        {account.display_name}
      </h3>
      {status.isPending && <p className="text-sm text-muted-foreground">{t('desktopNotifications.loading')}</p>}
      {status.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mailErrorMessage(t, status.error, t('desktopNotifications.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
      )}
      {folders?.length === 0 && <p className="text-sm text-muted-foreground">{t('desktopNotifications.noFolders')}</p>}
      {folders !== undefined && folders.length > 0 && (
        <>
          <ul
            aria-label={t('desktopNotifications.folders', { account: account.display_name })}
            className="flex flex-col divide-y rounded-lg border"
          >
            {folders.map((folder, position) => {
              const inputId = `${headingId}-${String(position)}`
              return (
                <li key={folder.path} className="flex items-center gap-3 p-3">
                  <input
                    id={inputId}
                    type="checkbox"
                    checked={notifies(chosen, account.id, folder.path)}
                    disabled={saving}
                    onChange={(event) => void toggle(folder.path, event.target.checked)}
                  />
                  <label htmlFor={inputId} className="min-w-0 truncate text-sm font-medium">
                    {folder.path}
                  </label>
                </li>
              )
            })}
          </ul>
          <p className="text-xs text-muted-foreground">{t('desktopNotifications.indexedOnly')}</p>
        </>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}
