import { useCallback, useMemo, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearch } from '@tanstack/react-router'

import { accountErrorMessage } from '@/accounts/messages.ts'
import { listAccounts } from '@/api/accounts.ts'
import { listMessages, searchMessages, type SearchQuery } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { MailSidebar } from './mail-sidebar.tsx'
import { MessageList } from './message-list.tsx'
import { MessageView } from './message-view.tsx'
import { SearchForm } from './search-form.tsx'

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
  const open = search.message
  return (
    <main className="flex flex-1 flex-col border-t lg:h-0 lg:min-h-0 lg:flex-row">
      <h1 className="sr-only">{t('mail.title')}</h1>
      <MailSidebar
        accounts={accounts.value.map((account) => account.id)}
        accountId={accountId}
        folder={search.q === undefined ? folder : null}
        className={cn(open !== undefined && 'hidden lg:flex')}
      />
      <section
        aria-label={t('list.label')}
        className={cn(
          'flex min-w-0 flex-col gap-3 border-t p-3 lg:w-96 lg:shrink-0 lg:overflow-y-auto lg:border-t-0 lg:border-r',
          open !== undefined && 'hidden lg:flex',
        )}
      >
        <SearchForm
          key={JSON.stringify(search.q ?? null)}
          accounts={accounts.value.map((account) => account.id)}
          accountId={accountId}
          folder={folder}
          query={search.q}
        />
        <Listing accountId={accountId} folder={folder} />
      </section>
      <section
        aria-label={t('message.label')}
        className={cn(
          'min-w-0 flex-1 flex-col p-3 lg:flex lg:overflow-y-auto',
          open === undefined ? 'hidden' : 'flex',
        )}
      >
        {open === undefined ? (
          <p className="m-auto text-sm text-muted-foreground">{t('message.none')}</p>
        ) : (
          <MessageView key={open} messageRef={open} />
        )}
      </section>
    </main>
  )
}

/** The folder's messages, or the search's when there is one; a new source starts over. */
function Listing({ accountId, folder }: { accountId: string; folder: string }): ReactElement {
  const { q } = useSearch({ from: '/mail' })
  // A string stands for the search: `q` is a new object on every navigation.
  const queryKey = q === undefined ? null : JSON.stringify(q)
  const query = useMemo(() => (queryKey === null ? null : (JSON.parse(queryKey) as SearchQuery)), [queryKey])
  const loadPage = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      query === null
        ? listMessages(accountId, { folder, cursor }, signal)
        : searchMessages(query, { cursor }, signal),
    [accountId, folder, query],
  )
  const key = queryKey === null ? `folder:${accountId}:${folder}` : `search:${queryKey}`
  return <MessageList key={key} loadPage={loadPage} searching={q !== undefined} />
}
