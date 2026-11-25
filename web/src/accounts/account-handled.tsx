import { useEffect, useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'

import { accountHandled, saveHandled, type Account, type HandledSetting } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import { accountErrorMessage } from '@/accounts/messages.ts'
import { HANDLED_KINDS, HANDLED_NAMES, readHandled, writeHandled, type HandledKind } from '@/accounts/handled.ts'
import { useFeature } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

/** What the account keeps the Handled mark as, and the form that changes it. */
export function AccountHandled({ account, onChange }: { account: Account; onChange: () => void }): ReactElement {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const mark = readHandled(account.handled)

  function close(): void {
    setEditing(false)
    // The form that held focus is gone.
    requestAnimationFrame(() => trigger.current?.focus())
  }

  if (editing) {
    return (
      <HandledForm
        account={account}
        onCancel={close}
        onSaved={() => {
          close()
          onChange()
        }}
      />
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <span>{t(`accounts.handled.summary.${mark.kind}`, { name: mark.name })}</span>
      <Button
        ref={trigger}
        variant="outline"
        size="sm"
        aria-label={t('accounts.handled.changeFor', { account: account.display_name })}
        onClick={() => {
          setEditing(true)
        }}
      >
        {t('accounts.handled.change')}
      </Button>
    </div>
  )
}

function HandledForm({
  account,
  onCancel,
  onSaved,
}: {
  account: Account
  onCancel: () => void
  onSaved: () => void
}): ReactElement {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const filters = useFeature('filters')
  const prefix = useId()
  const current = readHandled(account.handled)
  const [kind, setKind] = useState<HandledKind>(current.kind)
  const [names, setNames] = useState({ ...HANDLED_NAMES, ...(current.kind === 'star' ? {} : { [current.kind]: current.name }) })
  /** The rule sets the change is about, as shown: what a save confirms. */
  const [setting, setSetting] = useState<HandledSetting | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const form = useRef<HTMLFormElement>(null)
  const loaded = setting !== null

  useEffect(() => {
    const controller = new AbortController()
    accountHandled(account.id, controller.signal).then(setSetting, (failure: unknown) => {
      if (!controller.signal.aborted) {
        setError(accountErrorMessage(t, failure, t('accounts.handled.loadFailed')))
      }
    })
    return () => {
      controller.abort()
    }
  }, [account.id, attempt, t])

  useEffect(() => {
    form.current?.querySelector<HTMLInputElement>('input:checked')?.focus()
  }, [])

  const mark = writeHandled({ kind, name: kind === 'star' ? '' : names[kind].trim() })
  const changes = setting !== null && mark !== setting.mark

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (setting === null) {
      return
    }
    setSaving(true)
    setError(null)
    try {
      await saveHandled(account.id, mark, setting.rule_sets.map((ruleSet) => ruleSet.id))
      // Every message read says whether it is Handled as the account kept it.
      void queryClient.invalidateQueries()
      onSaved()
    } catch (failure) {
      setSaving(false)
      setError(
        failure instanceof ApiRequestError && failure.code === 'invalid_argument'
          ? t('accountErrors.handledInvalid')
          : accountErrorMessage(t, failure, t('accounts.handled.saveFailed')),
      )
      if (failure instanceof ApiRequestError && failure.code === 'handled_rule_sets') {
        setAttempt((value) => value + 1)
      }
    }
  }

  const id = (field: string): string => `${prefix}-${field}`
  return (
    <form
      ref={form}
      aria-labelledby={id('title')}
      className="flex flex-col gap-3 rounded-lg border p-3 text-sm"
      onSubmit={(event) => void submit(event)}
    >
      <h2 id={id('title')} className="font-medium">
        {t('accounts.handled.formTitle', { account: account.display_name })}
      </h2>
      <p className="text-muted-foreground">{t('accounts.handled.intro')}</p>
      <fieldset className="flex flex-col gap-2">
        <legend className="sr-only">{t('accounts.handled.title')}</legend>
        {HANDLED_KINDS.map((option) => (
          <div key={option} className="flex items-start gap-2">
            <input
              id={id(option)}
              type="radio"
              name={id('kind')}
              value={option}
              checked={kind === option}
              aria-describedby={id(`${option}-hint`)}
              className="mt-1"
              onChange={() => {
                setKind(option)
                setError(null)
              }}
            />
            <div className="flex flex-col">
              <label htmlFor={id(option)}>{t(`accounts.handled.kinds.${option}`)}</label>
              <span id={id(`${option}-hint`)} className="text-muted-foreground">
                {t(`accounts.handled.hints.${option}`)}
              </span>
            </div>
          </div>
        ))}
      </fieldset>
      {kind !== 'star' && (
        <div className="flex max-w-64 flex-col gap-1">
          <label htmlFor={id('name')} className="font-medium">
            {t(kind === 'keyword' ? 'accounts.handled.keywordName' : 'accounts.handled.labelName')}
          </label>
          <Input
            id={id('name')}
            required
            maxLength={64}
            value={names[kind]}
            onChange={(event) => {
              setNames({ ...names, [kind]: event.target.value })
            }}
          />
        </div>
      )}
      {changes && (filters || setting.rule_sets.length > 0) && (
        <div role="status">
          {setting.rule_sets.length === 0 ? (
            <p className="text-muted-foreground">{t('accounts.handled.noRuleSets')}</p>
          ) : (
            <>
              <p>{t('accounts.handled.ruleSets')}</p>
              <ul className="list-disc pl-5">
                {setting.rule_sets.map((ruleSet) => (
                  <li key={ruleSet.id}>
                    {ruleSet.account_id === 'all' ? t('accounts.handled.ruleSetsAll', { name: ruleSet.name }) : ruleSet.name}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {error !== null && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving || !loaded}>
          {t('accounts.handled.save')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          {t('accounts.cancel')}
        </Button>
      </div>
    </form>
  )
}
