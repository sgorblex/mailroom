import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { AccountsContext } from '@/accounts/accounts-context.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { type AccountId, listAccounts, type Account } from '@/api/accounts.ts'
import { ALL_INBOXES, listMessages, searchMessages, viewMessages, type MatchScope, type SearchQuery, type ViewKey } from '@/api/mail.ts'
import { useConversations } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { readDevicePref, writeDevicePref } from '@/lib/device-prefs.ts'
import { cn } from '@/lib/utils.ts'

import { CommandPalette } from './command-palette.tsx'
import { ComposeBar } from './compose-bar.tsx'
import { useCompose } from './compose-context.ts'
import { ComposeProvider } from './compose-provider.tsx'
import { Composer } from './composer.tsx'
import { FolderPane } from './folder-pane-view.tsx'
import { findNode, folderTree, hasListedSubfolders } from './folder-tree.ts'
import { MAIL_NS } from './i18n.ts'
import { indexScope, scopeReads } from './index-progress.ts'
import { IndexingNotice } from './indexing-notice.tsx'
import { useLiveEvents, useLiveStatus, useLiveViewing } from './live-context.ts'
import { ListPane } from './list-pane.tsx'
import { MailHeader } from './mail-header.tsx'
import { MessageList } from './message-list.tsx'
import { useReadingPane } from './reading-pane.ts'
import { ReadingView } from './reading-view.tsx'
import { SearchForm } from './search-form.tsx'
import { BulkHost } from './select-matching.tsx'
import { NewSenderHost } from './new-sender-dialog.tsx'
import { ScheduledList } from './scheduled-sends.tsx'
import { ShortcutSheet, ShortcutSheetButton } from './shortcut-sheet.tsx'
import { SnoozedList } from './snoozed-list.tsx'
import { ComposeAnnouncer } from './send-status.tsx'
import { HeldToasts } from './held-toasts.tsx'
import { useMailShortcuts, usePaletteShortcut } from './shortcuts.ts'
import { storedSubfolders } from './subfolder-view.ts'
import { MuteProvider } from './mute-provider.tsx'
import { SnoozeProvider } from './snooze-provider.tsx'
import { ACCOUNTS_KEY, messagesKey, viewsQuery } from './queries.ts'
import { ToastHost } from './toast.tsx'
import { SpamFilterOffer } from './spam-filter-offer.tsx'
import { OutcomeAnnouncer, Toolbar } from './toolbar.tsx'
import { QuickFilters } from './quick-filters.tsx'
import { listQuery, type MailSearch } from './route-search.ts'
import { NotIndexedNotice } from './saved-views.tsx'
import { isSameFolder, loadedFolders, roleFolder, useTriage } from './triage-context.ts'
import { TriageProvider } from './triage-provider.tsx'
import { viewFolder, viewScopes, viewSource } from './views.ts'

const DEFAULT_FOLDER = 'INBOX'
/** The account last shown on this device, whose folders `/mail` without one shows. */
const ACCOUNT_PREF = 'mail.account'
const isAccountId = (value: unknown): value is AccountId | null => value === null || Number.isInteger(value)
const LIST_PANE_ID = 'mail-list'

/**
 * The mail client: accounts and folders, the message list (a folder or a search) and the open
 * message — three panes on a wide screen (the message right of the list, below it, or over it, as
 * the user chose), one after the other on a phone, where an open message takes the screen.
 */
export function MailScreen(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const search = useSearch({ from: '/mail', select: screenSearch, structuralSharing: true })
  const navigate = useNavigate()
  // Kept between visits, so coming back shows the screen at once; read again behind it, as the
  // Accounts page changes them without this cache.
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), refetchOnMount: 'always' })
  const [sheet, setSheet] = useState(false)
  const [palette, setPalette] = useState(false)
  const savedViews = useQuery(viewsQuery()).data
  const grouped = useConversations()
  // Read once: another tab switching account must not change the one this tab shows.
  const [remembered] = useState(() => readDevicePref<AccountId | null>(ACCOUNT_PREF, null, isAccountId))
  const routed = search.account
  const known = accounts.data?.some((account) => account.id === routed) === true
  // The account the route shows, kept for the next `/mail` without one. Written when the route
  // changes only, never in answer to another tab's write, so two tabs on two accounts settle.
  useEffect(() => {
    if (known && routed !== undefined) {
      writeDevicePref(ACCOUNT_PREF, routed)
    }
  }, [known, routed])
  const accountIds = useMemo(() => accounts.data?.map((account) => account.id) ?? [], [accounts.data])
  const openSheet = useCallback(() => {
    setSheet(true)
  }, [])
  const togglePalette = useCallback(() => {
    setPalette((open) => !open)
  }, [])

  if (accounts.status === 'pending') {
    return (
      <main className="flex-1 p-6">
        <p role="status" className="text-muted-foreground">
          {t('accounts.loading')}
        </p>
      </main>
    )
  }
  if (accounts.status === 'error') {
    return (
      <main className="flex flex-1 flex-col items-start gap-3 p-6">
        <p role="alert">{accountErrorMessage(t, accounts.error, t('accounts.loadFailed'))}</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void accounts.refetch()
          }}
        >
          {t('auth.retry')}
        </Button>
      </main>
    )
  }
  const first = accounts.data[0]
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

  const accountId =
    search.account ?? accounts.data.find((account) => account.id === remembered)?.id ?? first.id
  const folder = search.folder ?? DEFAULT_FOLDER
  const requested = search.sub ?? storedSubfolders(accountId, folder)
  const listed = listQuery(accountId, folder, search.q, search.only)
  const closeList = (): void => {
    void navigate({ to: '/mail', search: (previous) => ({ ...previous, list: undefined }), replace: true })
  }
  return (
    <AccountsContext value={accounts.data}>
      <ToastHost>
        <MuteProvider>
          <TriageProvider
            sourceKey={grouping(
              search.view === undefined ? sourceKey(accountId, folder, requested, listed) : viewSource(search.view),
              grouped,
            )}
            sourceFolder={
              search.view === undefined
                ? shownFolder(folder, listed)
                : viewFolder(search.view === ALL_INBOXES ? ALL_INBOXES : savedViews?.find((each) => each.id === search.view))
            }
            accounts={accountIds}
          >
            <SnoozeProvider>
              <ComposeProvider accounts={accounts.data} accountId={accountId}>
                <HeldToasts />
                <MailShortcuts accountId={accountId} onHelp={openSheet} onPalette={togglePalette} />
                <MailPanes
                  accounts={accounts.data}
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
                {search.list === 'snoozed' && <SnoozedList onClose={closeList} />}
                {search.list === 'scheduled' && <ScheduledList onClose={closeList} />}
                {palette && (
                  <CommandPalette
                    accounts={accounts.data}
                    accountId={accountId}
                    onClose={() => {
                      setPalette(false)
                    }}
                  />
                )}
              </ComposeProvider>
            </SnoozeProvider>
          </TriageProvider>
        </MuteProvider>
      </ToastHost>
    </AccountsContext>
  )
}

/**
 * The three panes. The reading pane sits where the user put it, right of the list or below it; or,
 * set off, an open message or the composer takes the list's place with Back (the message with the
 * toolbar acting on the selection), the list scrolled where it was underneath. On a narrow screen
 * (a phone) a header with the folders drawer, search and compose tops the list, which takes the
 * width, and an open message or composer takes the screen.
 */
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
  const search = useSearch({ from: '/mail', select: panesSearch, structuralSharing: true })
  const subfolders = useListedSubfolders(accountId, folder, requested)
  const { session, shown } = useCompose()
  const { position, wide, fullView } = useReadingPane()
  const open = search.message
  const covered = open !== undefined || shown
  const overlaid = wide && position === 'off' && covered
  // The list's toolbar on one row: above a reading pane below it.
  const compact = wide && position === 'below'
  // A search or a view holds no folder's viewing interest.
  const showsFolder = search.q === undefined && search.view === undefined
  useLiveViewing(showsFolder ? { account: accountId, folder } : null)
  const readingPane = useRef<HTMLElement>(null)
  useFocusClosedRow(overlaid, search.thread ?? open)
  const searchId = useId()
  const [searchOpen, setSearchOpen] = useState(false)
  // On a narrow screen the search form shows on request, and while a search is shown.
  const searchShown = wide || searchOpen || search.q !== undefined
  const shownFolder = showsFolder ? folder : null
  // Opened from the header: typed into once shown.
  const focusSearch = useRef(false)
  const focusSearchField = useCallback(() => {
    document.getElementById(searchId)?.querySelector<HTMLInputElement>('input')?.focus()
  }, [searchId])
  useEffect(() => {
    if (searchShown && focusSearch.current) {
      focusSearch.current = false
      focusSearchField()
    }
  }, [searchShown, focusSearchField])
  return (
    // On a wide screen it takes the window's height left under the header and no more, each pane
    // scrolling on its own: a flex basis of 0 px, not flex-1's 0 %, which a column only as high as
    // its content (min-h-dvh) reads as the content's height, letting the whole page scroll.
    <main className="flex flex-1 flex-col border-t lg:min-h-0 lg:basis-0 lg:flex-row">
      <h1 className="sr-only">{t('mail.title')}</h1>
      {wide ? (
        <FolderPane
          accounts={accounts}
          accountId={accountId}
          folder={shownFolder}
          view={search.view ?? null}
          subfolders={subfolders}
        />
      ) : (
        <MailHeader
          accounts={accounts}
          accountId={accountId}
          folder={shownFolder}
          view={search.view ?? null}
          subfolders={subfolders}
          searching={search.q !== undefined}
          searchId={searchId}
          searchShown={searchShown}
          onSearch={() => {
            // A search shown keeps its form: the button takes the cursor there.
            if (search.q !== undefined) {
              focusSearchField()
              return
            }
            focusSearch.current = !searchShown
            setSearchOpen(!searchShown)
          }}
          hidden={covered}
        />
      )}
      <div className={cn('relative flex min-w-0 flex-1 flex-col lg:min-h-0', position !== 'below' && 'lg:flex-row')}>
        {/* On a phone the open message hides the list, but not the toolbar acting on the selection. */}
        <ListPane
          id={LIST_PANE_ID}
          label={t('list.label')}
          pane={readingPane}
          position={position}
          overlaid={overlaid}
          className={cn(
            'min-w-0 flex-col gap-3 border-t p-3 lg:flex lg:overflow-y-auto lg:border-t-0',
            shown ? 'hidden' : 'flex',
          )}
        >
          {/* With the reading pane below, its height is short: Compose, the search and the
              shortcut sheet share one row. */}
          <div className={cn('flex gap-3', compact ? 'items-start' : 'flex-col')}>
            <div className={cn('flex items-start gap-2', compact ? 'max-w-sm shrink-0' : 'min-w-0')}>
              <div className="min-w-0 flex-1">
                {/* The narrow screen's header has the Compose button; a message taking the screen, none
                    starts one, but Back to the draft and the sent notices stay above it. */}
                <ComposeBar accountId={accountId} starts={wide ? 'all' : covered ? 'none' : 'import'} />
              </div>
              {wide && !compact && <ShortcutSheetButton onClick={onHelp} />}
            </div>
            <div
              id={searchId}
              className={cn(
                'flex flex-col gap-3',
                compact && 'min-w-0 flex-1',
                covered && 'hidden lg:flex',
                !searchShown && 'hidden',
              )}
            >
              <SearchForm
                key={JSON.stringify(search.q ?? null)}
                accounts={accounts}
                accountId={accountId}
                folder={folder}
                subfolders={subfolders}
                query={search.q}
                only={search.only}
                compact={compact}
              />
            </div>
            {compact && <ShortcutSheetButton onClick={onHelp} />}
          </div>
          {/* Over the list, the message carries the toolbar acting on the selection. */}
          {!overlaid && <Toolbar />}
          <SpamFilterOffer />
          {/* Takes what height the pane has left, the list scrolling within it. */}
          <div
            className={cn(
              'flex flex-col gap-3 lg:flex-1',
              position === 'below' ? 'lg:min-h-32' : 'lg:min-h-64',
              covered && 'hidden lg:flex',
            )}
          >
            {search.view === undefined ? (
              <Listing accounts={accounts} accountId={accountId} folder={folder} subfolders={subfolders} />
            ) : (
              <ViewListing key={search.view} accounts={accounts} view={search.view} />
            )}
          </div>
        </ListPane>
        <section
          ref={readingPane}
          aria-label={t('message.label')}
          className={cn(
            'min-w-0 flex-1 flex-col p-3 lg:min-h-0 lg:overflow-y-auto',
            covered ? 'flex' : 'hidden',
            // Room for the message's action bar, fixed at the foot of a narrow screen.
            !wide && 'pb-[calc(var(--action-bar,0px)+1rem)]',
            position === 'off' ? 'lg:absolute lg:inset-0 lg:bg-background' : 'lg:flex',
            position === 'off' && !covered && 'lg:hidden',
          )}
        >
          {fullView && open !== undefined && !shown && (
            <Link
              to="/mail"
              search={(previous) => ({ ...previous, message: undefined, thread: undefined })}
              className="mb-4 flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft aria-hidden="true" className="size-4" />
              {t('message.back')}
            </Link>
          )}
          {overlaid && !shown && <Toolbar className="mb-4" />}
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
              <ReadingView message={open} thread={search.thread} />
            ))}
        </section>
      </div>
      <OutcomeAnnouncer />
      <ComposeAnnouncer />
      <BulkHost />
      <NewSenderHost />
    </main>
  )
}

/**
 * Once the message open over the list (`overlaid`) closes, focus goes back to its row, where the
 * reader left the list, rather than to the page.
 */
function useFocusClosedRow(overlaid: boolean, shown: string | undefined): void {
  const last = useRef<string | undefined>(undefined)
  const wasOverlaid = useRef(false)
  useEffect(() => {
    const closed = wasOverlaid.current && !overlaid && shown === undefined ? last.current : undefined
    wasOverlaid.current = overlaid
    last.current = shown
    if (closed !== undefined) {
      document.getElementById(LIST_PANE_ID)?.querySelector<HTMLElement>(`[data-ref="${CSS.escape(closed)}"]`)?.focus()
    }
  }, [overlaid, shown])
}

/**
 * What each part reads of the URL, a selection: opening a message draws again the panes, which
 * show it, not the whole screen, and the list's own source not at all.
 */
function screenSearch({ account, folder, sub, q, only, view, list }: MailSearch): ScreenSearch {
  return { account, folder, sub, q, only, view, list }
}
type ScreenSearch = Pick<MailSearch, 'account' | 'folder' | 'sub' | 'q' | 'only' | 'view' | 'list'>

function panesSearch({ message, thread, q, only, view }: MailSearch): PanesSearch {
  return { message, thread, q, only, view }
}
type PanesSearch = Pick<MailSearch, 'message' | 'thread' | 'q' | 'only' | 'view'>

function listingSearch({ q, only }: MailSearch): Pick<MailSearch, 'q' | 'only'> {
  return { q, only }
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

/** The folder the list shows: the one open, or the search's (INBOX by default); `undefined` over every folder. */
function shownFolder(folder: string, q: SearchQuery | undefined): string | undefined {
  const shown = q === undefined ? folder : (q.folder ?? DEFAULT_FOLDER)
  return shown === '*' ? undefined : shown
}

/** Names the list shown: a folder, or a search (`q` is a new object on every navigation). */
function sourceKey(accountId: AccountId, folder: string, subfolders: boolean, q: SearchQuery | undefined): string {
  if (q === undefined) {
    return `folder:${String(accountId)}:${subfolders ? '1' : '0'}:${folder}`
  }
  return `search:${searchesSubfolders(accountId, folder, subfolders, q) ? '1' : '0'}:${JSON.stringify(q)}`
}

/** The list `source` names, by conversation when `grouped`: its pages are not the same. */
function grouping(source: string, grouped: boolean): string {
  return grouped ? `${source}:thread` : source
}

/**
 * The folder's messages, or the search's when there is one, narrowed by the quick filters, by
 * conversation when the user groups them; a new source starts over.
 */
const Listing = memo(function Listing({
  accounts,
  accountId,
  folder,
  subfolders,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string
  subfolders: boolean
}): ReactElement {
  const { q, only } = useSearch({ from: '/mail', select: listingSearch, structuralSharing: true })
  const listed = listQuery(accountId, folder, q, only)
  const grouped = useConversations()
  const key = grouping(sourceKey(accountId, folder, subfolders, listed), grouped)
  const searchKey = listed === undefined ? null : JSON.stringify(listed)
  const query = useMemo(() => (searchKey === null ? null : (JSON.parse(searchKey) as SearchQuery)), [searchKey])
  const merged = query === null ? subfolders : searchesSubfolders(accountId, folder, subfolders, query)
  const loadPage = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      query === null
        ? listMessages(accountId, { folder, subfolders: merged, cursor, grouped }, signal)
        : searchMessages(query, { subfolders: merged, cursor, grouped }, signal),
    [accountId, folder, query, merged, grouped],
  )
  // What "select all matching" acts on: the search, or the folder as a search with no criteria.
  const match = useMemo<MatchScope>(
    () => ({ query: query ?? { account: String(accountId), folder }, subfolders: merged }),
    [accountId, folder, query, merged],
  )
  // What the composer does changes these two folders' lists under the user's eyes.
  const { revision } = useCompose()
  const { folders } = useTriage()
  const accountFolders = loadedFolders(folders[accountId])
  const shownFolder = accountFolders?.find((each) => isSameFolder(each.path, folder))
  const delimiter = (shownFolder ?? accountFolders?.[0])?.delimiter ?? null
  const accountIds = useMemo(() => accounts.map((account) => account.id), [accounts])
  const scope = useMemo(
    () => indexScope(accountIds, accountId, folder, merged, delimiter, query),
    [accountIds, accountId, folder, merged, delimiter, query],
  )
  const live = q === undefined && (shownFolder?.role === 'drafts' || shownFolder?.role === 'sent')
  const queryClient = useQueryClient()
  const readRevision = useRef(revision)
  useEffect(() => {
    if (readRevision.current !== revision) {
      readRevision.current = revision
      if (live) {
        void queryClient.invalidateQueries({ queryKey: messagesKey(key, null) })
      }
    }
  }, [revision, live, key, queryClient])
  // A change to a folder the list reads reads its first page again; a search the index never
  // answers (a body's text) is not run again by itself.
  useLiveEvents((event) => {
    if (event.type === 'sync' || scope === null) {
      return
    }
    const allMail = roleFolder(loadedFolders(folders[event.account]), 'all')
    if (scopeReads(scope, event.account, event.folder, allMail !== undefined && isSameFolder(allMail.path, event.folder))) {
      void queryClient.invalidateQueries({ queryKey: messagesKey(key, null) })
    }
  })
  return (
    <>
      <QuickFilters only={only} />
      <LiveNotice />
      {scope !== null && <IndexingNotice scope={scope} />}
      <MessageList
        key={key}
        source={key}
        loadPage={loadPage}
        match={match}
        searching={q !== undefined}
        filtered={only !== undefined}
        subfolders={merged}
      />
    </>
  )
})

/**
 * A view's messages, read from the index only: its folders left out are named above the list. A
 * change to one of its folders reads its first page again, as does one of them getting ready.
 */
const ViewListing = memo(function ViewListing({ accounts, view }: { accounts: Account[]; view: ViewKey }): ReactElement {
  const grouped = useConversations()
  const source = grouping(viewSource(view), grouped)
  const loadPage = useCallback(
    (cursor: string | null, signal: AbortSignal) => viewMessages(view, cursor, signal, grouped),
    [view, grouped],
  )
  const saved = useQuery(viewsQuery()).data
  const shown = view === ALL_INBOXES ? ALL_INBOXES : saved?.find((each) => each.id === view)
  const scopes = useMemo(() => (shown === undefined ? [] : viewScopes(shown, accounts)), [shown, accounts])
  const { folders } = useTriage()
  const queryClient = useQueryClient()
  useLiveEvents((event) => {
    if (event.type === 'sync' && event.state !== 'ready') {
      return
    }
    const listed = loadedFolders(folders[event.account])
    const allMail = roleFolder(listed, 'all')
    // Without the account's folders, which name its All Mail, any of its changes may be the view's.
    const reads = scopes.some((scope) =>
      listed === undefined
        ? scope.accounts.includes(event.account)
        : scopeReads(scope, event.account, event.folder, allMail !== undefined && isSameFolder(allMail.path, event.folder)),
    )
    if (reads) {
      void queryClient.invalidateQueries({ queryKey: messagesKey(source, null) })
    }
  })
  return (
    <>
      <LiveNotice />
      <NotIndexedNotice source={source} loadPage={loadPage} />
      <MessageList source={source} loadPage={loadPage} searching={false} view />
    </>
  )
})

/** A quiet line while the live updates stream is down and being opened again. */
function LiveNotice(): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  return useLiveStatus() === 'reconnecting' ? (
    <p role="status" className="text-xs text-muted-foreground">
      {t('list.reconnecting')}
    </p>
  ) : null
}
