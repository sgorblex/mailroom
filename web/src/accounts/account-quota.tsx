import { useCallback, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { accountQuota, type Account, type QuotaUsage } from '@/api/accounts.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { cn } from '@/lib/utils.ts'
import { formatSize } from '@/mail/format.ts'

/** From this share of the limit on, a bar warns. */
const WARN_AT = 0.9

/** The account's mailbox usage, asked of its server on its own so a slow or failing one holds up nothing else. */
export function AccountQuotaBars({ account }: { account: Account }): ReactElement | null {
  const { t } = useTranslation()
  const load = useCallback((signal: AbortSignal) => accountQuota(account.id, signal), [account.id])
  // A changed account (a new object after a reload) may be on another server: ask again.
  const { state, retry } = useLoad(load, (error) => accountErrorMessage(t, error, t('accounts.actionFailed')), account)

  if (state.kind === 'loading') {
    return null
  }
  if (state.kind === 'failed') {
    return (
      <p className="text-sm text-muted-foreground">
        {t('accounts.quota.failed', { reason: state.message })}{' '}
        <Button type="button" variant="link" size="xs" className="h-auto p-0" onClick={retry}>
          {t('accounts.quota.retry')}
        </Button>
      </p>
    )
  }
  const { storage, messages } = state.value
  if (storage === null && messages === null) {
    return <p className="text-sm text-muted-foreground">{t('accounts.quota.unknown')}</p>
  }
  return (
    <div className="flex flex-col gap-2">
      {storage !== null && <UsageBar account={account} kind="storage" usage={storage} />}
      {messages !== null && <UsageBar account={account} kind="messages" usage={messages} />}
    </div>
  )
}

function UsageBar({
  account,
  kind,
  usage,
}: {
  account: Account
  kind: 'storage' | 'messages'
  usage: QuotaUsage
}): ReactElement {
  const { t, i18n } = useTranslation()
  // A limit of 0 leaves nothing to use: it is over quota whatever is used, with no share to give.
  const share = usage.limit > 0 ? usage.used / usage.limit : null
  const over = share === null || usage.used > usage.limit
  const warn = over || share >= WARN_AT
  const amount = (value: number): string =>
    kind === 'storage' ? formatSize(i18n.language, value) : new Intl.NumberFormat(i18n.language).format(value)
  const used = amount(usage.used)
  const limit = amount(usage.limit)
  // The real share, over 100% when over the limit; only the bar stops at full.
  const text =
    share === null
      ? t('accounts.quota.usageNoShare', { used, limit })
      : t('accounts.quota.usage', {
          used,
          limit,
          percent: new Intl.NumberFormat(i18n.language, { style: 'percent' }).format(share),
        })
  const label = over
    ? t('accounts.quota.overQuota', { usage: text })
    : warn
      ? t('accounts.quota.nearlyFull', { usage: text })
      : text
  const filled = share === null ? 100 : Math.min(share, 1) * 100
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
        <span>{t(`accounts.quota.${kind}`)}</span>
        <span className={cn('tabular-nums', warn ? 'font-medium text-destructive' : 'text-muted-foreground')}>
          {label}
        </span>
      </div>
      <div
        role="meter"
        aria-label={t(`accounts.quota.${kind}Of`, { account: account.display_name })}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(filled)}
        aria-valuetext={label}
        data-warning={warn ? (over ? 'over' : 'nearly-full') : undefined}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn('h-full rounded-full', warn ? 'bg-destructive' : 'bg-primary')}
          style={{ width: `${String(filled)}%` }}
        />
      </div>
    </div>
  )
}
