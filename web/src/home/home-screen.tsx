import { useCallback, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useRouter } from '@tanstack/react-router'
import { AlarmClock, Clock, Inbox, Star, Tag } from 'lucide-react'

import { AccountName } from '@/accounts/account-name.tsx'
import type { Appearance } from '@/api/auth.ts'
import { fetchHome, type HomeSummary, type MailSummary, type RuleSetGlance } from '@/api/home.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { ALL_INBOXES } from '@/api/mail.ts'
import { useAppearance, useAuth, useTimeZone } from '@/auth/auth-context.ts'
import { navPages, type NavPath } from '@/auth/nav-pages.ts'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'

/** The page each `home` choice opens instead of the dashboard. */
const HOME_PAGES: Record<Exclude<Appearance['home'], 'dashboard'>, NavPath> = {
  mail: '/mail',
  filters: '/filters',
  accounts: '/accounts',
  activity: '/activity',
}

/**
 * Where `/` goes: when the app opens on it (the tab's first page, nothing to go back to), the page the
 * user chose to open on, while the instance offers it; else, and whenever Home is opened later, the
 * dashboard.
 */
export function HomeRoute(): ReactElement {
  const { appearance } = useAppearance()
  const opening = !useRouter().history.canGoBack()
  const offered = navPages(useAuth().features, false).map((page) => page.to)
  const target = appearance.home === 'dashboard' ? null : HOME_PAGES[appearance.home]
  if (opening && target !== null && offered.includes(target)) {
    return <Navigate to={target} replace />
  }
  return <HomeScreen />
}

/**
 * The dashboard: unread mail per account, what waits on the user, the filters' week, and a tile per
 * page the instance offers. A part whose feature the instance does not run is left out.
 */
export function HomeScreen(): ReactElement {
  const { t } = useTranslation()
  const load = useCallback((signal: AbortSignal) => fetchHome(signal), [])
  const { state, retry } = useLoad(load, (error) => apiErrorMessage(error, t('home.loadFailed')))
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 p-4 md:p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('home.title')}</h1>
      {state.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('home.loading')}
        </p>
      )}
      {state.kind === 'failed' && (
        <div className="flex flex-col items-start gap-2">
          <p role="alert">{state.message}</p>
          <Button variant="outline" size="sm" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {state.kind === 'loaded' && <Cards summary={state.value} />}
      <FeatureTiles />
    </main>
  )
}

function Cards({ summary }: { summary: HomeSummary }): ReactElement | null {
  const { mail, filters } = summary
  if (mail === null && filters === null) {
    return null
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {mail !== null && <UnreadCard mail={mail} />}
      {mail !== null && <WaitingCard mail={mail} />}
      {filters !== null && <FiltersCard ruleSets={filters.rule_sets} />}
    </div>
  )
}

function Card({ id, title, children, className }: { id: string; title: string; children: ReactNode; className?: string }): ReactElement {
  return (
    <section aria-labelledby={id} className={cn('flex flex-col gap-3 rounded-lg border bg-card p-4', className)}>
      <h2 id={id} className="font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

const ROW = 'flex items-center justify-between gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-accent'

/** A count, or a dash read as "not known yet" while the index holds no copy. */
function Count({ value }: { value: number | null }): ReactElement {
  const { t } = useTranslation()
  if (value === null) {
    return (
      <span className="text-muted-foreground" title={t('home.notIndexed')}>
        <span aria-hidden="true">–</span>
        <span className="sr-only">{t('home.notIndexed')}</span>
      </span>
    )
  }
  return <span className={cn('tabular-nums', value > 0 ? 'font-semibold' : 'text-muted-foreground')}>{value}</span>
}

function UnreadCard({ mail }: { mail: MailSummary }): ReactElement {
  const { t } = useTranslation()
  return (
    <Card id="home-unread" title={t('home.unread.title')}>
      <ul className="flex flex-col">
        <li>
          <Link to="/mail" search={{ view: ALL_INBOXES }} className={cn(ROW, 'font-medium')}>
            <span className="flex items-center gap-2">
              <Inbox aria-hidden="true" className="size-4" />
              {t('home.unread.allInboxes')}
            </span>
            <Count value={mail.unread} />
          </Link>
        </li>
        {mail.inboxes.map((inbox) => (
          <li key={inbox.account_id}>
            <Link to="/mail" search={{ account: inbox.account_id, folder: 'INBOX' }} className={ROW}>
              <AccountName account={inbox} className="pl-6" />
              <Count value={inbox.unread} />
            </Link>
          </li>
        ))}
      </ul>
      {mail.inboxes.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t('home.unread.noAccounts')}{' '}
          <Link to="/accounts" className="text-foreground underline underline-offset-4">
            {t('home.unread.addAccount')}
          </Link>
        </p>
      )}
    </Card>
  )
}

function WaitingCard({ mail }: { mail: MailSummary }): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const next = mail.next_scheduled_at
  return (
    <Card id="home-waiting" title={t('home.waiting.title')}>
      <ul className="flex flex-col">
        <li>
          <Link to="/mail" search={{ q: { account: '*', folder: '*', flagged: true } }} className={ROW}>
            <span className="flex items-center gap-2">
              <Star aria-hidden="true" className="size-4" />
              {t('home.waiting.starred')}
            </span>
            <Count value={mail.starred} />
          </Link>
        </li>
        <li>
          <Link to="/mail" search={{ q: { account: '*', folder: '*', to_sort: true } }} className={ROW}>
            <span className="flex items-center gap-2">
              <Tag aria-hidden="true" className="size-4" />
              {t('home.waiting.toSort')}
            </span>
            <Count value={mail.to_sort} />
          </Link>
        </li>
        <li>
          <Link to="/mail" search={{ list: 'snoozed' }} className={ROW}>
            <span className="flex flex-col">
              <span className="flex items-center gap-2">
                <AlarmClock aria-hidden="true" className="size-4" />
                {t('home.waiting.snoozedToday')}
              </span>
              {mail.snoozed > 0 && (
                <span className="pl-6 text-xs text-muted-foreground">{t('home.waiting.snoozedAll', { count: mail.snoozed })}</span>
              )}
            </span>
            <Count value={mail.snoozed_today} />
          </Link>
        </li>
        <li>
          <Link to="/mail" search={{ list: 'scheduled' }} className={ROW}>
            <span className="flex flex-col">
              <span className="flex items-center gap-2">
                <Clock aria-hidden="true" className="size-4" />
                {t('home.waiting.scheduled')}
              </span>
              {next !== null && (
                <span className="pl-6 text-xs text-muted-foreground">
                  {t('home.waiting.next', { when: formatDateTime(i18n.language, next, timeZone) })}
                </span>
              )}
              {mail.scheduled_failed > 0 && (
                <span className="pl-6 text-xs font-medium text-destructive">
                  {t('home.waiting.scheduledFailed', { count: mail.scheduled_failed })}
                </span>
              )}
            </span>
            <Count value={mail.scheduled} />
          </Link>
        </li>
      </ul>
    </Card>
  )
}

function FiltersCard({ ruleSets }: { ruleSets: RuleSetGlance[] }): ReactElement {
  const { t } = useTranslation()
  const { features } = useAuth()
  const activity = navPages(features, false).some((page) => page.to === '/activity')
  return (
    <Card id="home-filters" title={t('home.filters.title')} className="md:col-span-2">
      {ruleSets.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('home.filters.none')}</p>
      ) : (
        <ul className="flex flex-col divide-y">
          {ruleSets.map((ruleSet) => (
            <RuleSetRow key={ruleSet.id} ruleSet={ruleSet} />
          ))}
        </ul>
      )}
      <p className="flex flex-wrap gap-4 text-sm">
        <Link to="/filters" className="underline underline-offset-4">
          {t('home.filters.open')}
        </Link>
        {activity && (
          <Link to="/activity" className="underline underline-offset-4">
            {t('home.filters.activity')}
          </Link>
        )}
      </p>
    </Card>
  )
}

function RuleSetRow({ ruleSet }: { ruleSet: RuleSetGlance }): ReactElement {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const failing = ruleSet.failed_runs > 0 || ruleSet.last_run_failed || ruleSet.disabled_reason !== null
  return (
    <li className="flex flex-col gap-0.5 py-2 text-sm sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <span className="flex min-w-0 items-center gap-2">
        {failing && <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-destructive" />}
        <Link
          to="/filters/$ruleSetId"
          params={{ ruleSetId: String(ruleSet.id) }}
          className="truncate font-medium underline-offset-4 hover:underline"
        >
          {ruleSet.name}
        </Link>
        {!ruleSet.enabled && <span className="shrink-0 text-xs text-muted-foreground">{t('home.filters.off')}</span>}
      </span>
      <span className="flex flex-wrap gap-x-3 text-muted-foreground">
        <span>{t('home.filters.runs', { count: ruleSet.runs })}</span>
        {ruleSet.failed_runs > 0 && (
          <span className="font-medium text-destructive">{t('home.filters.failed', { count: ruleSet.failed_runs })}</span>
        )}
        <span>{t('home.filters.actedOn', { count: ruleSet.acted_on })}</span>
        {ruleSet.disabled_reason !== null ? (
          <span className="font-medium text-destructive">{t('home.filters.turnedOff')}</span>
        ) : (
          ruleSet.last_run_at !== null && (
            <span className={cn(ruleSet.last_run_failed && 'font-medium text-destructive')}>
              {t(ruleSet.last_run_failed ? 'home.filters.lastFailed' : 'home.filters.last', {
                when: formatDateTime(i18n.language, ruleSet.last_run_at, timeZone),
              })}
            </span>
          )
        )}
      </span>
    </li>
  )
}

/** The pages a tile leads to, in this order, as the instance offers them. */
const TILES: readonly NavPath[] = ['/mail', '/filters', '/accounts', '/activity', '/profile']

function FeatureTiles(): ReactElement {
  const { t } = useTranslation()
  const { features } = useAuth()
  const pages = navPages(features, false)
  const tiles = TILES.flatMap((to) => pages.filter((page) => page.to === to))
  return (
    <nav aria-labelledby="home-pages">
      <h2 id="home-pages" className="sr-only">
        {t('home.pages')}
      </h2>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {tiles.map((page) => {
          const Icon = page.icon
          return (
            <li key={page.to}>
              <Link
                to={page.to}
                className="flex h-full flex-col gap-1 rounded-lg border bg-card p-3 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <Icon aria-hidden="true" className="size-5 text-primary" />
                <span className="font-medium">{t(page.label)}</span>
                <span className="text-xs text-muted-foreground">{t(`home.tiles.${tileKey(page.to)}`)}</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

const tileKey = (to: NavPath): string => to.slice(1)
