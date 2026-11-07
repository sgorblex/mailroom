import { useCallback, useMemo, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearch } from '@tanstack/react-router'

import { accountErrorMessage } from '@/accounts/messages.ts'
import { listAccounts } from '@/api/accounts.ts'
import { listMessages, searchMessages, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'

import { ComposeBar } from './compose-bar.tsx'
import { useCompose } from './compose-context.ts'
import { ComposeProvider } from './compose-provider.tsx'
import { Composer } from './composer.tsx'
import { MAIL_NS } from './i18n.ts'
import { MailSidebar } from './mail-sidebar.tsx'
import { MessageList } from './message-list.tsx'
import { MessageView } from './message-view.tsx'
import { SearchForm } from './search-form.tsx'
import { ComposeAnnouncer } from './send-status.tsx'
import { useMailShortcuts } from './shortcuts.ts'
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
  return (
    <TriageProvider sourceKey={sourceKey(accountId, folder, search.q)}>
      <ComposeProvider accounts={accounts.value} accountId={accountId}>
        <MailShortcuts accountId={accountId} />
        <MailPanes accounts={accounts.value.map((account) => account.id)} accountId={accountId} folder={folder} />
      </ComposeProvider>
    </TriageProvider>
  )
}

/** The three panes; on a phone an open message or composer takes the screen. */
function MailPanes({
  accounts,
  accountId,
  folder,
}: {
  accounts: string[]
  accountId: string
  folder: string
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const search = useSearch({ from: '/mail' })
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
        <ComposeBar accountId={accountId} />
        <div className={cn('flex flex-col gap-3', covered && 'hidden lg:flex')}>
          <SearchForm
            key={JSON.stringify(search.q ?? null)}
            accounts={accounts}
            accountId={accountId}
            folder={folder}
            query={search.q}
          />
        </div>
        <Toolbar />
        <div className={cn('flex flex-col gap-3', covered && 'hidden lg:flex')}>
          <Listing accountId={accountId} folder={folder} />
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
    </main>
  )
}

function MailShortcuts({ accountId }: { accountId: string }): null {
  useMailShortcuts(accountId)
  return null
}

/** Names the list shown: a folder, or a search (`q` is a new object on every navigation). */
function sourceKey(accountId: string, folder: string, q: SearchQuery | undefined): string {
  return q === undefined ? `folder:${accountId}:${folder}` : `search:${JSON.stringify(q)}`
}

/** The folder's messages, or the search's when there is one; a new source starts over. */
function Listing({ accountId, folder }: { accountId: string; folder: string }): ReactElement {
  const { q } = useSearch({ from: '/mail' })
  const key = sourceKey(accountId, folder, q)
  const query = useMemo(() => (key.startsWith('search:') ? (JSON.parse(key.slice(7)) as SearchQuery) : null), [key])
  const loadPage = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      query === null
        ? listMessages(accountId, { folder, cursor }, signal)
        : searchMessages(query, { cursor }, signal),
    [accountId, folder, query],
  )
  // What the composer does changes these two folders' lists under the user's eyes.
  const { revision } = useCompose()
  const shownFolder = loadedFolders(useTriage().folders[accountId])?.find((each) => isSameFolder(each.path, folder))
  const live = q === undefined && (shownFolder?.role === 'drafts' || shownFolder?.role === 'sent')
  return <MessageList key={key} loadPage={loadPage} searching={q !== undefined} reloadKey={live ? revision : undefined} />
}
