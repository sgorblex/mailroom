import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useId, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { accountVacation, listAccounts, saveVacation, type Account, type VacationReply } from '@/api/accounts.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from '@/mail/messages.ts'
import { ACCOUNTS_KEY, READ_ON_MOUNT, vacationKey } from '@/mail/queries.ts'

// TipTap is most of the editor's weight: the profile loads without it.
const RichEditor = lazy(() => import('@/mail/rich-editor.tsx').then((module) => ({ default: module.RichEditor })))

const SELECT = 'h-9 min-w-0 rounded-md border bg-background px-2 text-sm text-foreground'

/**
 * Each account's vacation auto-reply: on or off, its days, the identity it goes from and the
 * reply, written in the composer's rich editor. The worker answers a person's new mail with it,
 * once per sender every few days.
 */
export function VacationSettings(): ReactElement {
  const { t } = useTranslation()
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), ...READ_ON_MOUNT })
  return (
    <section aria-labelledby="vacation" className="flex flex-col gap-3">
      <h2 id="vacation" className="font-medium">
        {t('vacation.title')}
      </h2>
      <p className="text-sm text-muted-foreground">{t('vacation.intro')}</p>
      {accounts.isError && (
        <p role="alert" className="text-sm text-destructive">
          {apiErrorMessage(accounts.error, t('vacation.loadFailed'))}
        </p>
      )}
      {accounts.data?.map((account) => <AccountVacation key={account.id} account={account} />)}
    </section>
  )
}

function AccountVacation({ account }: { account: Account }): ReactElement {
  const { t } = useTranslation()
  const vacation = useQuery({
    queryKey: vacationKey(account.id),
    queryFn: ({ signal }) => accountVacation(account.id, signal),
    ...READ_ON_MOUNT,
  })
  const headingId = `vacation-${String(account.id)}`
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h3 id={headingId} className="text-sm font-medium">
        {account.display_name}
      </h3>
      {vacation.isPending && <p className="text-sm text-muted-foreground">{t('vacation.loading')}</p>}
      {vacation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mailErrorMessage(t, vacation.error, t('vacation.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
      )}
      {vacation.data !== undefined && <VacationForm account={account} saved={vacation.data} />}
    </section>
  )
}

type Outcome = { kind: 'idle' } | { kind: 'saved'; answering: boolean; indexed: boolean } | { kind: 'failed'; message: string }

function VacationForm({ account, saved }: { account: Account; saved: VacationReply }): ReactElement {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const id = useId()
  const [enabled, setEnabled] = useState(saved.enabled)
  const [startsOn, setStartsOn] = useState(saved.starts_on ?? '')
  const [endsOn, setEndsOn] = useState(saved.ends_on ?? '')
  const [identity, setIdentity] = useState(saved.identity ?? '')
  const [subject, setSubject] = useState(saved.subject)
  const [html, setHtml] = useState(saved.html)
  const [text, setText] = useState(saved.text)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  function changed(): void {
    setOutcome({ kind: 'idle' })
  }

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (enabled && (subject.trim() === '' || text.trim() === '')) {
      setOutcome({ kind: 'failed', message: t('vacation.incomplete') })
      return
    }
    if (startsOn !== '' && endsOn !== '' && endsOn < startsOn) {
      setOutcome({ kind: 'failed', message: t('vacation.daysOutOfOrder') })
      return
    }
    setSaving(true)
    setOutcome({ kind: 'idle' })
    try {
      const kept = await saveVacation(account.id, {
        enabled,
        starts_on: startsOn === '' ? null : startsOn,
        ends_on: endsOn === '' ? null : endsOn,
        subject,
        html,
        identity: identity === '' ? null : identity,
      })
      queryClient.setQueryData(vacationKey(account.id), kept)
      setOutcome({ kind: 'saved', answering: kept.answering, indexed: kept.indexed })
    } catch (failure) {
      setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('vacation.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={enabled}
          onChange={(event) => {
            setEnabled(event.target.checked)
            changed()
          }}
        />
        {t('vacation.enabled')}
      </label>
      <div className="flex flex-wrap gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t('vacation.startsOn')}
          <Input
            type="date"
            value={startsOn}
            onChange={(event) => {
              setStartsOn(event.target.value)
              changed()
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t('vacation.endsOn')}
          <Input
            type="date"
            value={endsOn}
            onChange={(event) => {
              setEndsOn(event.target.value)
              changed()
            }}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        {t('vacation.identity')}
        <select
          className={SELECT}
          value={identity}
          onChange={(event) => {
            setIdentity(event.target.value)
            changed()
          }}
        >
          <option value="">{t('vacation.addressed')}</option>
          {account.identities.map((each) => (
            <option key={each.address} value={each.address.toLowerCase()}>
              {each.address}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        {t('vacation.subject')}
        <Input
          value={subject}
          maxLength={200}
          onChange={(event) => {
            setSubject(event.target.value)
            changed()
          }}
        />
      </label>
      <span id={`${id}-body`} className="text-sm">
        {t('vacation.body')}
      </span>
      <Suspense fallback={<p className="text-sm text-muted-foreground">{t('vacation.loading')}</p>}>
        <RichEditor
          focusStart={false}
          labelId={`${id}-body`}
          html={saved.html}
          inline={[]}
          locked={saving}
          onChange={(changedHtml, changedText) => {
            setHtml(changedHtml)
            setText(changedText)
            changed()
          }}
          onImages={() => null}
          onFiles={() => undefined}
          placeholder={t('vacation.placeholder')}
        />
      </Suspense>
      {outcome.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      )}
      {outcome.kind === 'saved' && (
        <p role="status" className="text-sm">
          {!outcome.answering ? t('vacation.saved') : outcome.indexed ? t('vacation.savedAnswering') : t('vacation.savedNotIndexed')}
        </p>
      )}
      {saved.enabled && !saved.indexed && <p className="text-sm text-muted-foreground">{t('vacation.notIndexed')}</p>}
      <div>
        <Button type="submit" size="sm" disabled={saving}>
          {t('vacation.save')}
        </Button>
      </div>
    </form>
  )
}
