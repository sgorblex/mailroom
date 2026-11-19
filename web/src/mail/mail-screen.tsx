import { useCallback, useMemo, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearch } from '@tanstack/react-router'

import { AccountsContext } from '@/accounts/accounts-context.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { type AccountId, listAccounts, type Account } from '@/api/accounts.ts'
import { listMessages, searchMessages, type MatchScope, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'

import { CommandPalette } from './command-palette.tsx'
import { ComposeBar } from './compose-bar.tsx'
import { useCompose } from './compose-context.ts'
import { ComposeProvider } from './compose-provider.tsx'
import { Composer } from './composer.tsx'
import { findNode, folderTree, hasListedSubfolders } from './folder-tree.ts'
import { MAIL_NS } from './i18n.ts'
import { MailSidebar } from './mail-sidebar.tsx'
import { MessageList } from './message-list.tsx'
import { MessageView } from './message-view.tsx'
import { SearchForm } from './search-form.tsx'
import { BulkHost } from './select-matching.tsx'
import { ShortcutSheet, ShortcutSheetButton } from './shortcut-sheet.tsx'
import { ComposeAnnouncer } from './send-status.tsx'
import { useMailShortcuts, usePaletteShortcut } from './shortcuts.ts'
import { storedSubfolders } from './subfolder-view.ts'
import { ToastHost } from './toast.tsx'
import { SpamFilterOffer } from './spam-filter-offer.tsx'
import { OutcomeAnnouncer, Toolbar } from './toolbar.tsx'
import { isSameFolder, loadedFolders, useTriage } from './triage-context.ts'
import { TriageProvider } from './triage-provider.tsx'

const DEFAULT_FOLDER = 'INBOX'

/**
 * The mail client: accounts and folders, the message list (a folder or a search) and the open
 * message — three panes side by side on a wide screen, one after the other on a phone, where an
 * open message takes the screen.
 */
export function MailScreen(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const search = useSearch({ from: '/mail' })
  const { state: accounts, retry } = useLoad(listAccounts, (error) =>
    accountErrorMessage(t, error, t('accounts.loadFailed')),
  )
  const [sheet, setSheet] = useState(false)
  const [palette, setPalette] = useState(false)

  if (accounts.kind === 'loading') {
    return (
      <main className="flex-1 p-6">
        <p role="status" className="text-muted-foreground">
          {t('accounts.loading')}
        </p>
      </main>
    )
  }
  if (accounts.kind === 'failed') {
    return (
      <main className="flex flex-1 flex-col items-start gap-3 p-6">
        <p role="alert">{accounts.message}</p>
        <Button variant="outline" size="sm" onClick={retry}>
          {t('auth.retry')}
        </Button>
      </main>
    )
  }
  const first = accounts.value[0]
  if (first === undefined) {
    return (
      <main className="flex-1 p-6">
        <p className="text-muted-foreground">
          {t('mail.noAccounts')}{' '}
          <Link to="/accounts" className="text-foreground underline underline-offset-4">
            {t('mail.goToAccounts')}
          </Link>
        </p>
      </main>
    )
  }

  const accountId = search.account ?? first.id
  const folder = search.folder ?? DEFAULT_FOLDER
  const requested = search.sub ?? storedSubfolders(accountId, folder)
  const accountIds = accounts.value.map((account) => account.id)
  const openSheet = (): void => {
    setSheet(true)
  }
  return (
    <AccountsContext value={accounts.value}>
      <ToastHost>
        <TriageProvider sourceKey={sourceKey(accountId, folder, requested, search.q)} accounts={accountIds}>
          <ComposeProvider accounts={accounts.value} accountId={accountId}>
            <MailShortcuts
              accountId={accountId}
              onHelp={openSheet}
              onPalette={() => {
                setPalette((open) => !open)
              }}
            />
            <MailPanes
              accounts={accounts.value}
              accountId={accountId}
              folder={folder}
              requested={requested}
              onHelp={openSheet}
            />
            {sheet && (
              <ShortcutSheet
                onClose={() => {
                  setSheet(false)
                }}
              />
            )}
            {palette && (
              <CommandPalette
                accounts={accounts.value}
                accountId={accountId}
                onClose={() => {
                  setPalette(false)
                }}
              />
            )}
          </ComposeProvider>
        </TriageProvider>
      </ToastHost>
    </AccountsContext>
  )
}

/** The three panes; on a phone an open message or composer takes the screen. */
function MailPanes({
  accounts,
  accountId,
  folder,
  requested,
  onHelp,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string
  /** The folder is to be listed with its subfolders (the URL's `sub`, else this browser's choice). */
  requested: boolean
  /** Opens the shortcut sheet. */
  onHelp: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const search = useSearch({ from: '/mail' })
  const subfolders = useListedSubfolders(accountId, folder, requested)
  const { session, shown } = useCompose()
  const open = search.message
  const covered = open !== undefined || shown
  return (
    <main className="flex flex-1 flex-col border-t lg:h-0 lg:min-h-0 lg:flex-row">
      <h1 className="sr-only">{t('mail.title')}</h1>
      <MailSidebar
        accounts={accounts}
        accountId={accountId}
        folder={search.q === undefined ? folder : null}
        subfolders={subfolders}
        className={cn(covered && 'hidden lg:flex')}
      />
      {/* On a phone the open message hides the list, but not the toolbar acting on it. */}
      <section
        aria-label={t('list.label')}
        className={cn(
          'min-w-0 flex-col gap-3 border-t p-3 lg:flex lg:w-96 lg:shrink-0 lg:overflow-y-auto lg:border-t-0 lg:border-r',
          shown ? 'hidden' : 'flex',
        )}
      >
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <ComposeBar accountId={accountId} />
          </div>
          <ShortcutSheetButton onClick={onHelp} />
        </div>
        <div className={cn('flex flex-col gap-3', covered && 'hidden lg:flex')}>
          <SearchForm
            key={JSON.stringify(search.q ?? null)}
            accounts={accounts}
            accountId={accountId}
            folder={folder}
            subfolders={subfolders}
            query={search.q}
          />
        </div>
        <Toolbar />
        <SpamFilterOffer />
        <div className={cn('flex flex-col gap-3', covered && 'hidden lg:flex')}>
          <Listing accountId={accountId} folder={folder} subfolders={subfolders} />
        </div>
      </section>
      <section
        aria-label={t('message.label')}
        className={cn(
          'min-w-0 flex-1 flex-col p-3 lg:flex lg:overflow-y-auto',
          covered ? 'flex' : 'hidden',
        )}
      >
        {/* Stays mounted behind an open message: what is being written lives in it. */}
        {session !== null && (
          <div hidden={!shown} className={shown ? 'flex flex-1 flex-col' : undefined}>
            <Composer key={session.id} session={session} />
          </div>
        )}
        {!shown &&
          (open === undefined ? (
            <p className="m-auto text-sm text-muted-foreground">{t('message.none')}</p>
          ) : (
            <MessageView key={open} messageRef={open} />
          ))}
      </section>
      <OutcomeAnnouncer />
      <ComposeAnnouncer />
      <BulkHost />
    </main>
  )
}

function MailShortcuts({
  accountId,
  onHelp,
  onPalette,
}: {
  accountId: AccountId
  onHelp: () => void
  onPalette: () => void
}): null {
  useMailShortcuts(accountId, onHelp)
  usePaletteShortcut(onPalette)
  return null
}

/**
 * Whether the folder is listed with its subfolders: as `requested`, while the account's folders
 * load; then only if it still has subfolders the list can read, so a choice the tree no longer
 * offers to undo (the subfolders gone, or too many) does not hold.
 */
function useListedSubfolders(accountId: AccountId, folder: string, requested: boolean): boolean {
  const folders = loadedFolders(useTriage().folders[accountId])
  return useMemo(() => {
    if (!requested || folders === undefined) {
      return requested
    }
    const node = findNode(folderTree(folders), folder)
    return node !== undefined && hasListedSubfolders(node)
  }, [requested, folders, folder])
}

/**
 * Whether the search reads the folder shown with its subfolders: when they are listed with it and
 * the search is in that folder of that account.
 */
function searchesSubfolders(accountId: AccountId, folder: string, subfolders: boolean, q: SearchQuery): boolean {
  return subfolders && q.account === String(accountId) && q.folder !== undefined && isSameFolder(q.folder, folder)
}

/** Names the list shown: a folder, or a search (`q` is a new object on every navigation). */
function sourceKey(accountId: AccountId, folder: string, subfolders: boolean, q: SearchQuery | undefined): string {
  if (q === undefined) {
    return `folder:${String(accountId)}:${subfolders ? '1' : '0'}:${folder}`
  }
  return `search:${searchesSubfolders(accountId, folder, subfolders, q) ? '1' : '0'}:${JSON.stringify(q)}`
}

/** The folder's messages, or the search's when there is one; a new source starts over. */
function Listing({ accountId, folder, subfolders }: { accountId: AccountId; folder: string; subfolders: boolean }): ReactElement {
  const { q } = useSearch({ from: '/mail' })
  const key = sourceKey(accountId, folder, subfolders, q)
  const searchKey = q === undefined ? null : JSON.stringify(q)
  const query = useMemo(() => (searchKey === null ? null : (JSON.parse(searchKey) as SearchQuery)), [searchKey])
  const merged = query === null ? subfolders : searchesSubfolders(accountId, folder, subfolders, query)
  const loadPage = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      query === null
        ? listMessages(accountId, { folder, subfolders: merged, cursor }, signal)
        : searchMessages(query, { subfolders: merged, cursor }, signal),
    [accountId, folder, query, merged],
  )
  // What "select all matching" acts on: the search, or the folder as a search with no criteria.
  const match = useMemo<MatchScope>(
    () => ({ query: query ?? { account: String(accountId), folder }, subfolders: merged }),
    [accountId, folder, query, merged],
  )
  // What the composer does changes these two folders' lists under the user's eyes.
  const { revision } = useCompose()
  const { folders, reloads } = useTriage()
  const shownFolder = loadedFolders(folders[accountId])?.find((each) => isSameFolder(each.path, folder))
  const live = q === undefined && (shownFolder?.role === 'drafts' || shownFolder?.role === 'sent')
  return (
    <MessageList
      key={key}
      loadPage={loadPage}
      match={match}
      searching={q !== undefined}
      subfolders={merged}
      // An undo moves messages back: every list reads itself again.
      reloadKey={`${live ? String(revision) : '-'}:${String(reloads)}`}
    />
  )
}
