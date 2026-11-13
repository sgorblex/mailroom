import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'
import { FolderPlus } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { FolderDialogs, type FolderDialog, type FolderDone } from './folder-dialogs.tsx'
import { accountDelimiter, isWithin, renamedPath } from './folder-tree.ts'
import { FolderTree } from './folder-tree-view.tsx'
import { MAIL_NS } from './i18n.ts'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from './messages.ts'
import { isSameFolder, useAccountFolders } from './triage-context.ts'

/**
 * The user's accounts; the one shown lists its folders with their unread counts, and creates,
 * renames and deletes them. `folder` is the folder shown, `null` while a search is.
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

/** What the last folder action did: said politely, or as an alert while it needs the user. */
interface Notice {
  text: string
  alert: boolean
}

function FolderList({ accountId, folder }: { accountId: string; folder: string | null }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  // Shared with the toolbar, whose actions change the counts in place; read again on mount.
  const { state, retry } = useAccountFolders(accountId, true)
  const [dialog, setDialog] = useState<FolderDialog | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [reveal, setReveal] = useState<string | null>(null)
  const folders = state.kind === 'loaded' ? state.value : []
  const delimiter = accountDelimiter(folders)

  function done(result: FolderDone): void {
    setDialog(null)
    retry()
    const show = (path: string | null): void => {
      void navigate({ to: '/mail', search: path === null ? { account: accountId } : { account: accountId, folder: path } })
    }
    switch (result.kind) {
      case 'created':
        setReveal(result.path)
        setNotice({ text: t('folderActions.created', { path: result.path }), alert: false })
        break
      case 'renamed': {
        const { answer } = result
        setReveal(answer.path)
        if (folder !== null && isWithin(folder, answer.folder, delimiter)) {
          show(renamedPath(folder, answer.folder, answer.path, delimiter))
        }
        setNotice(
          !answer.rule_sets_updated
            ? { text: t('folderActions.ruleSetsNotUpdated', { path: answer.path }), alert: true }
            : {
                text:
                  answer.rule_sets.length > 0
                    ? t('folderActions.renamedRuleSets', { path: answer.path, count: answer.rule_sets.length })
                    : t('folderActions.renamed', { path: answer.path }),
                alert: false,
              },
        )
        break
      }
      case 'deleted':
        if (folder !== null && isSameFolder(folder, result.path)) {
          show(null)
        }
        setNotice({ text: t('folderActions.deleted', { path: result.path }), alert: false })
        break
    }
  }

  const header = (
    <div className="flex items-center gap-1">
      <h2 id={`mail-account-${accountId}`} className="min-w-0 flex-1 truncate px-2 text-sm font-medium">
        {accountId}
      </h2>
      {state.kind === 'loaded' && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t('folderActions.newTop', { id: accountId })}
          title={t('folderActions.newTop', { id: accountId })}
          onClick={() => {
            setNotice(null)
            setDialog({ kind: 'create', parent: null })
          }}
        >
          <FolderPlus aria-hidden="true" />
        </Button>
      )}
    </div>
  )

  if (state.kind === 'loading') {
    return (
      <>
        {header}
        <p role="status" className="px-2 text-sm text-muted-foreground">
          {t('folders.loading')}
        </p>
      </>
    )
  }
  if (state.kind === 'failed') {
    return (
      <>
        {header}
        <div className="flex flex-col items-start gap-2 px-2">
          <p role="alert" className="text-sm">
            {mailErrorMessage(t, state.error, t('folders.loadFailed'), ACCOUNT_NOT_FOUND)}
          </p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      </>
    )
  }
  return (
    <>
      {header}
      <FolderTree
        key={accountId}
        accountId={accountId}
        folders={state.value}
        current={folder}
        reveal={reveal}
        label={t('folders.of', { id: accountId })}
        onAction={(next) => {
          setNotice(null)
          setDialog(next)
        }}
      />
      <div role="status" className="px-2 text-xs text-muted-foreground empty:hidden">
        {notice !== null && !notice.alert ? notice.text : null}
      </div>
      {notice?.alert === true && (
        <div role="alert" className="flex flex-col items-start gap-1 px-2 text-xs">
          <p>{notice.text}</p>
          <Button
            variant="link"
            size="xs"
            className="h-auto px-0"
            onClick={() => {
              setNotice(null)
            }}
          >
            {t('folderActions.dismiss')}
          </Button>
        </div>
      )}
      {dialog !== null && (
        <FolderDialogs
          accountId={accountId}
          folders={state.value}
          dialog={dialog}
          onCancel={() => {
            setDialog(null)
          }}
          onDone={done}
        />
      )}
    </>
  )
}
