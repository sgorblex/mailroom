import { useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { FolderPlus } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import type { AccountId, Account } from '@/api/accounts.ts'
import type { ViewKey } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { FolderDialogs, type FolderDialog, type FolderDone } from './folder-dialogs.tsx'
import { accountDelimiter, isWithin, renamedPath } from './folder-tree.ts'
import { FolderTree } from './folder-tree-view.tsx'
import { MAIL_NS } from './i18n.ts'
import { MARK_READ_CONFIRM_ABOVE, markReadError, markScopeRead, type MarkReadOutcome, type MarkReadScope } from './mark-folder-read.ts'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from './messages.ts'
import { NOTIFICATIONS_KEY } from './queries.ts'
import { ViewsSection } from './saved-views.tsx'
import { forgetStoredSubfolders, renameStoredSubfolders } from './subfolder-view.ts'
import { isSameFolder, useAccountFolders, useTriage } from './triage-context.ts'

/**
 * The user's views, then their accounts; the one shown lists its folders with their unread counts,
 * and creates, renames, deletes and marks them read. `folder` is the folder shown, `null` while a
 * search or a view (`view`) is; `subfolders`, whether it is listed with its subfolders.
 */
export function MailSidebar({
  accounts,
  accountId,
  folder,
  view,
  subfolders,
  className,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string | null
  view: ViewKey | null
  subfolders: boolean
  className?: string
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <nav
      aria-label={t('folders.label')}
      className={cn('flex flex-col gap-4 p-3 lg:w-60 lg:shrink-0 lg:overflow-y-auto lg:border-r', className)}
    >
      <ViewsSection accounts={accounts} accountId={accountId} current={view} />
      {accounts.map((account) =>
        account.id === accountId ? (
          <section key={account.id} aria-labelledby={`mail-account-${String(account.id)}`} className="flex flex-col gap-1">
            <FolderList account={account} folder={folder} subfolders={subfolders} />
          </section>
        ) : (
          <Link
            key={account.id}
            to="/mail"
            search={{ account: account.id }}
            className="flex rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <AccountName account={account} />
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

function FolderList({
  account,
  folder,
  subfolders,
}: {
  account: Account
  folder: string | null
  subfolders: boolean
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const accountId = account.id
  // Shared with the toolbar, whose actions change the counts in place; read again on mount.
  const { state, retry } = useAccountFolders(accountId, true)
  const { folderMarkedRead } = useTriage()
  const [dialog, setDialog] = useState<FolderDialog | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [reveal, setReveal] = useState<string | null>(null)
  // The folders with a mark-read under way, which a second click leaves alone.
  const marking = useRef(new Set<string>())
  const folders = state.kind === 'loaded' ? state.value : []
  const delimiter = accountDelimiter(folders)

  function done(result: FolderDone): void {
    setDialog(null)
    retry()
    const show = (path: string | null): void => {
      void navigate({
        to: '/mail',
        search: path === null ? { account: accountId } : { account: accountId, folder: path, sub: subfolders },
      })
    }
    switch (result.kind) {
      case 'created':
        setReveal(result.path)
        setNotice({ text: t('folderActions.created', { path: result.path }), alert: false })
        break
      case 'renamed': {
        const { answer } = result
        setReveal(answer.path)
        renameStoredSubfolders(accountId, answer.folder, answer.path, delimiter)
        // The server moved the desktop-notification choice along with the folder.
        void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY })
        if (folder !== null && isWithin(folder, answer.folder, delimiter)) {
          show(renamedPath(folder, answer.folder, answer.path, delimiter))
        }
        const everyAccount = answer.all_accounts_rule_sets.length
        // Rule sets for all accounts keep the old path, which other accounts may still have.
        const stale = everyAccount > 0 ? ` ${t('folderActions.allAccountsRuleSets', { count: everyAccount })}` : ''
        setNotice(
          !answer.rule_sets_updated
            ? { text: t('folderActions.ruleSetsNotUpdated', { path: answer.path }) + stale, alert: true }
            : {
                text:
                  (answer.rule_sets.length > 0
                    ? t('folderActions.renamedRuleSets', { path: answer.path, count: answer.rule_sets.length })
                    : t('folderActions.renamed', { path: answer.path })) + stale,
                alert: everyAccount > 0,
              },
        )
        break
      }
      case 'deleted':
        forgetStoredSubfolders(accountId, result.path, delimiter)
        void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY })
        if (folder !== null && isSameFolder(folder, result.path)) {
          show(null)
        }
        setNotice({ text: t('folderActions.deleted', { path: result.path }), alert: false })
        break
      case 'markedRead':
        folderMarkedRead(accountId, result.outcome.done)
        setNotice(markedReadNotice(result.scope, result.outcome))
        break
    }
  }

  function markedReadNotice(scope: MarkReadScope, outcome: MarkReadOutcome): Notice {
    const path = scope.folder.path
    const [first] = outcome.failed
    if (first === undefined) {
      const key = scope.subfolders ? 'folderActions.markedReadSubtree' : 'folderActions.markedRead'
      return { text: t(key, { path, count: outcome.marked }), alert: false }
    }
    if (outcome.done.length === 0) {
      return { text: markReadError(t, first.error), alert: true }
    }
    const folders = outcome.failed.map(({ folder: failed }) => failed.path).join(', ')
    return {
      text: t('folderActions.markedReadPartly', { count: outcome.marked, folders, reason: markReadError(t, first.error) }),
      alert: true,
    }
  }

  function act(next: FolderDialog): void {
    setNotice(null)
    if (next.kind !== 'markRead' || next.scope.unread > MARK_READ_CONFIRM_ABOVE) {
      setDialog(next)
      return
    }
    const { scope } = next
    const paths = scope.folders.map((each) => each.path)
    if (paths.some((path) => marking.current.has(path))) {
      return
    }
    for (const path of paths) {
      marking.current.add(path)
    }
    void markScopeRead(accountId, scope)
      .then((outcome) => {
        // Lists the folders again, after a refusal too: one may have changed since.
        done({ kind: 'markedRead', scope, outcome })
      })
      .finally(() => {
        for (const path of paths) {
          marking.current.delete(path)
        }
      })
  }

  const header = (
    <div className="flex items-center gap-1">
      <h2 id={`mail-account-${String(accountId)}`} className="flex min-w-0 flex-1 px-2 text-sm font-medium">
        <AccountName account={account} />
      </h2>
      {state.kind === 'loaded' && (
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t('folderActions.newTop', { account: account.display_name })}
          title={t('folderActions.newTop', { account: account.display_name })}
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
        subfolders={subfolders}
        reveal={reveal}
        label={t('folders.of', { account: account.display_name })}
        onAction={act}
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
          onStale={retry}
        />
      )}
    </>
  )
}
