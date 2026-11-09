import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { addAccount, changeAccount, listAccounts, removeAccount, testAccount, type Account } from '@/api/accounts.ts'
import { setRunFilters } from '@/api/filters.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { AccountForm } from '@/accounts/account-form.tsx'
import { accountErrorMessage, loginTestMessage } from '@/accounts/messages.ts'
import { useFeature } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Switch } from '@/components/ui/switch.tsx'

type ListState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'loaded'; accounts: Account[] }

/** The signed-in user's mail accounts: add, edit, test, remove and, with filters, Run filters. */
export function AccountsScreen(): ReactElement {
  const { t } = useTranslation()
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [adding, setAdding] = useState(false)
  const addTrigger = useRef<HTMLButtonElement>(null)
  const reload = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    listAccounts(controller.signal).then(
      (accounts) => {
        setList({ kind: 'loaded', accounts })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setList({ kind: 'failed', message: apiErrorMessage(error, t('accounts.loadFailed')) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt, t])

  function closeAdding(): void {
    setAdding(false)
    // The form that held focus is gone.
    requestAnimationFrame(() => addTrigger.current?.focus())
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('accounts.title')}</h1>
      {list.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('accounts.loading')}
        </p>
      )}
      {list.kind === 'failed' && (
        <div className="flex flex-col items-start gap-3">
          <p role="alert">{list.message}</p>
          <Button variant="outline" size="sm" onClick={reload}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {list.kind === 'loaded' && list.accounts.length === 0 && (
        <p className="text-muted-foreground">{t('accounts.none')}</p>
      )}
      {list.kind === 'loaded' && list.accounts.length > 0 && (
        <ul aria-label={t('accounts.title')} className="flex flex-col divide-y rounded-lg border">
          {list.accounts.map((account) => (
            <AccountRow key={account.id} account={account} onChange={reload} />
          ))}
        </ul>
      )}
      {adding ? (
        <AccountForm
          save={async (id, fields) => {
            await addAccount({ ...fields, id, password: fields.password ?? '' })
            setAdding(false)
            reload()
          }}
          onCancel={closeAdding}
        />
      ) : (
        <Button
          ref={addTrigger}
          className="self-start"
          onClick={() => {
            setAdding(true)
          }}
        >
          {t('accounts.add')}
        </Button>
      )}
    </main>
  )
}

type RowMode = 'idle' | 'editing' | 'confirming-removal'

function AccountRow({ account, onChange }: { account: Account; onChange: () => void }): ReactElement {
  const { t } = useTranslation()
  const filters = useFeature('filters')
  const [mode, setMode] = useState<RowMode>('idle')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [runFilters, setRunFiltersState] = useState(account.run_filters === true)
  const editTrigger = useRef<HTMLButtonElement>(null)
  const removeTrigger = useRef<HTMLButtonElement>(null)
  const confirmRemoval = useRef<HTMLButtonElement>(null)
  /** The mode being left, so focus goes back to the button that opened it. */
  const returnFocus = useRef<RowMode | null>(null)

  useEffect(() => {
    if (mode === 'confirming-removal') {
      confirmRemoval.current?.focus()
    } else if (mode === 'idle' && returnFocus.current !== null) {
      const trigger = returnFocus.current === 'editing' ? editTrigger : removeTrigger
      returnFocus.current = null
      trigger.current?.focus()
    }
  }, [mode])

  function enter(next: RowMode): void {
    if (next === 'idle') {
      returnFocus.current = mode
    }
    setMode(next)
    setError(null)
    setTestResult(null)
  }

  async function test(): Promise<void> {
    setPending(true)
    setError(null)
    setTestResult(null)
    try {
      const result = await testAccount(account.id)
      setTestResult({ ok: result.ok, message: loginTestMessage(t, result) })
    } catch (failure) {
      setError(accountErrorMessage(t, failure, t('accounts.actionFailed')))
    } finally {
      setPending(false)
    }
  }

  async function remove(): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await removeAccount(account.id)
      // The row goes with the reload; `pending` stays so nothing is sent twice.
      onChange()
    } catch (failure) {
      setError(accountErrorMessage(t, failure, t('accounts.actionFailed')))
      setPending(false)
    }
  }

  async function toggleRunFilters(): Promise<void> {
    setPending(true)
    setError(null)
    try {
      setRunFiltersState(await setRunFilters(account.id, !runFilters))
    } catch (failure) {
      setError(accountErrorMessage(t, failure, t('accounts.actionFailed')))
    } finally {
      setPending(false)
    }
  }

  if (mode === 'editing') {
    return (
      <li className="p-3">
        <AccountForm
          account={account}
          save={async (id, fields) => {
            await changeAccount(id, fields)
            returnFocus.current = 'editing'
            setMode('idle')
            onChange()
          }}
          onCancel={() => {
            enter('idle')
          }}
        />
      </li>
    )
  }
  return (
    <li className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-col">
          <span className="font-medium">{account.id}</span>
          <span className="text-sm text-muted-foreground">
            {account.login} · {account.imap.host}:{String(account.imap.port)}
          </span>
          {!account.has_password && (
            <span className="text-sm text-destructive">{t('accounts.noPassword')}</span>
          )}
        </div>
        {mode === 'idle' && (
          <span className="ml-auto flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={pending}
              aria-label={t('accounts.testAccount', { id: account.id })}
              onClick={() => void test()}
            >
              {t('accounts.test')}
            </Button>
            <Button
              ref={editTrigger}
              variant="outline"
              size="sm"
              aria-label={t('accounts.editTitle', { id: account.id })}
              onClick={() => {
                enter('editing')
              }}
            >
              {t('accounts.edit')}
            </Button>
            <Button
              ref={removeTrigger}
              variant="outline"
              size="sm"
              aria-label={t('accounts.removeAccount', { id: account.id })}
              onClick={() => {
                enter('confirming-removal')
              }}
            >
              {t('accounts.remove')}
            </Button>
          </span>
        )}
      </div>
      {filters && (
        <div className="flex items-center gap-3">
          <Switch
            checked={runFilters}
            aria-label={t('accounts.runFiltersOn', { id: account.id })}
            aria-describedby={`account-${account.id}-run-filters-hint`}
            disabled={pending}
            onCheckedChange={() => void toggleRunFilters()}
          />
          <span className="text-sm">{t('accounts.runFilters')}</span>
          <span id={`account-${account.id}-run-filters-hint`} className="text-sm text-muted-foreground">
            {runFilters ? t('accounts.runFiltersOnHint') : t('accounts.runFiltersOffHint')}
          </span>
        </div>
      )}
      {mode === 'confirming-removal' && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm">{t('accounts.confirmRemoval', { id: account.id })}</p>
          <Button
            ref={confirmRemoval}
            variant="destructive"
            size="sm"
            disabled={pending}
            onClick={() => void remove()}
          >
            {t('accounts.confirmRemove')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              enter('idle')
            }}
          >
            {t('accounts.cancel')}
          </Button>
        </div>
      )}
      {testResult !== null && (
        <p role={testResult.ok ? 'status' : 'alert'} className={testResult.ok ? 'text-sm' : 'text-sm text-destructive'}>
          {testResult.message}
        </p>
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}
