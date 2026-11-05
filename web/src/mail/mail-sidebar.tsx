import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'
import {
  Archive,
  FilePen,
  Folder,
  Inbox,
  Mails,
  Send,
  ShieldAlert,
  Star,
  Trash2,
  type LucideIcon,
} from 'lucide-react'

import type { FolderRole, MailFolder } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { ACCOUNT_NOT_FOUND, folderName, mailErrorMessage } from './messages.ts'
import { useAccountFolders } from './triage-context.ts'

const ROLE_ICONS: Record<FolderRole, LucideIcon> = {
  inbox: Inbox,
  sent: Send,
  drafts: FilePen,
  trash: Trash2,
  junk: ShieldAlert,
  archive: Archive,
  all: Mails,
  flagged: Star,
}

/**
 * The user's accounts; the one shown lists its folders with their unread counts. `folder` is the
 * folder shown, `null` while a search is.
 */
export function MailSidebar({
  accounts,
  accountId,
  folder,
  className,
}: {
  accounts: string[]
  accountId: string
  folder: string | null
  className?: string
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <nav
      aria-label={t('folders.label')}
      className={cn('flex flex-col gap-4 p-3 lg:w-60 lg:shrink-0 lg:overflow-y-auto lg:border-r', className)}
    >
      {accounts.map((id) =>
        id === accountId ? (
          <section key={id} aria-labelledby={`mail-account-${id}`} className="flex flex-col gap-1">
            <h2 id={`mail-account-${id}`} className="truncate px-2 text-sm font-medium">
              {id}
            </h2>
            <FolderList accountId={id} folder={folder} />
          </section>
        ) : (
          <Link
            key={id}
            to="/mail"
            search={{ account: id }}
            className="truncate rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            {id}
          </Link>
        ),
      )}
    </nav>
  )
}

function FolderList({ accountId, folder }: { accountId: string; folder: string | null }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  // Shared with the toolbar, whose actions change the counts in place; read again on mount.
  const { state, retry } = useAccountFolders(accountId, true)

  if (state.kind === 'loading') {
    return (
      <p role="status" className="px-2 text-sm text-muted-foreground">
        {t('folders.loading')}
      </p>
    )
  }
  if (state.kind === 'failed') {
    return (
      <div className="flex flex-col items-start gap-2 px-2">
        <p role="alert" className="text-sm">
          {mailErrorMessage(t, state.error, t('folders.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
        <Button variant="outline" size="sm" onClick={retry}>
          {t('auth.retry')}
        </Button>
      </div>
    )
  }
  return (
    <ul aria-label={t('folders.of', { id: accountId })} className="flex flex-col">
      {state.value.map((item) => (
        <li key={item.path}>
          <FolderLink accountId={accountId} folder={item} current={item.path === folder} />
        </li>
      ))}
    </ul>
  )
}

function FolderLink({
  accountId,
  folder,
  current,
}: {
  accountId: string
  folder: MailFolder
  current: boolean
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const Icon = folder.role === null ? Folder : ROLE_ICONS[folder.role]
  const name = folderName(t, folder)
  return (
    <Link
      to="/mail"
      search={{ account: accountId, folder: folder.path }}
      title={folder.path}
      aria-current={current ? 'page' : undefined}
      className={cn(
        'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent',
        current ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {folder.unread > 0 && (
        <>
          <span aria-hidden="true" className="text-xs font-medium tabular-nums text-foreground">
            {folder.unread}
          </span>
          <span className="sr-only">{`, ${t('folders.unread', { count: folder.unread })}`}</span>
        </>
      )}
    </Link>
  )
}
