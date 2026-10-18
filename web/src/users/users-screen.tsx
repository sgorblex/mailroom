import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type SubmitEvent,
} from 'react'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { createUser, listUsers, removeUser, resetPassword, type ListedUser } from '@/api/users.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { userErrorMessage } from '@/users/messages.ts'
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from '@/users/password.ts'

type ListState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | { kind: 'loaded'; users: ListedUser[] }

/** Admins add and remove users and reset their passwords. */
export function UsersScreen(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const isAdmin = auth.status === 'signed-in' && auth.user.is_admin
  const [list, setList] = useState<ListState>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const reload = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  useEffect(() => {
    if (!isAdmin) {
      return
    }
    const controller = new AbortController()
    listUsers(controller.signal).then(
      (users) => {
        setList({ kind: 'loaded', users })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setList({ kind: 'failed', message: apiErrorMessage(error, t('users.loadFailed')) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [attempt, isAdmin, t])

  if (!isAdmin) {
    return (
      <main className="mx-auto w-full max-w-2xl flex-1 p-6">
        <p role="alert">{t('users.adminsOnly')}</p>
      </main>
    )
  }
  const self = auth.user.username
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('users.title')}</h1>
      {list.kind === 'loading' && (
        <p role="status" className="text-muted-foreground">
          {t('users.loading')}
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
      {list.kind === 'loaded' && (
        <ul aria-label={t('users.title')} className="flex flex-col divide-y rounded-lg border">
          {list.users.map((user) => (
            <UserRow key={user.id} user={user} isSelf={user.username === self} onChange={reload} />
          ))}
        </ul>
      )}
      <AddUserForm onAdded={reload} />
    </main>
  )
}

type RowMode = 'idle' | 'resetting' | 'confirming-removal'

function UserRow({
  user,
  isSelf,
  onChange,
}: {
  user: ListedUser
  isSelf: boolean
  onChange: () => void
}): ReactElement {
  const { t } = useTranslation()
  const [mode, setMode] = useState<RowMode>('idle')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const resetTrigger = useRef<HTMLButtonElement>(null)
  const removeTrigger = useRef<HTMLButtonElement>(null)
  /** The mode being left, so focus goes back to the button that opened it. */
  const returnFocus = useRef<RowMode | null>(null)
  const passwordInput = useRef<HTMLInputElement>(null)
  const confirmRemoval = useRef<HTMLButtonElement>(null)

  // The button that was clicked is gone once the mode changes, so focus moves on by hand.
  useEffect(() => {
    if (mode === 'resetting') {
      passwordInput.current?.focus()
    } else if (mode === 'confirming-removal') {
      confirmRemoval.current?.focus()
    } else if (returnFocus.current !== null) {
      const trigger = returnFocus.current === 'resetting' ? resetTrigger : removeTrigger
      returnFocus.current = null
      trigger.current?.focus()
    }
  }, [mode])

  function enter(next: RowMode): void {
    if (next === 'idle') {
      returnFocus.current = mode
    }
    setMode(next)
    setPassword('')
    setError(null)
    setNotice(null)
  }

  /** `pending` stays set after success when `done` replaces the row, so nothing is sent twice. */
  async function run(
    action: () => Promise<unknown>,
    done: () => void,
    { keepPending = false } = {},
  ): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await action()
      done()
      if (!keepPending) {
        setPending(false)
      }
    } catch (failure) {
      setError(userErrorMessage(t, failure, t('users.actionFailed')))
      setPending(false)
    }
  }

  function reset(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    void run(
      () => resetPassword(user.id, password),
      () => {
        enter('idle')
        setNotice(t('users.passwordReset'))
      },
    )
  }

  const errorId = `user-${String(user.id)}-error`
  return (
    <li className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{user.username}</span>
        {user.is_admin && <span className="text-sm text-muted-foreground">{t('users.admin')}</span>}
        {isSelf && <span className="text-sm text-muted-foreground">{t('users.you')}</span>}
        <span className="ml-auto flex gap-2">
          {mode === 'idle' && !isSelf && (
            <>
              <Button
                ref={resetTrigger}
                variant="outline"
                size="sm"
                aria-label={t('users.resetPasswordOf', { username: user.username })}
                onClick={() => {
                  enter('resetting')
                }}
              >
                {t('users.resetPassword')}
              </Button>
              <Button
                ref={removeTrigger}
                variant="outline"
                size="sm"
                aria-label={t('users.removeUser', { username: user.username })}
                onClick={() => {
                  enter('confirming-removal')
                }}
              >
                {t('users.remove')}
              </Button>
            </>
          )}
        </span>
      </div>
      {mode === 'resetting' && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={reset}>
          <label htmlFor={`user-${String(user.id)}-password`} className="text-sm font-medium">
            {t('users.newPasswordFor', { username: user.username })}
          </label>
          <Input
            ref={passwordInput}
            id={`user-${String(user.id)}-password`}
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            maxLength={MAX_PASSWORD_LENGTH}
            className="max-w-xs"
            value={password}
            aria-invalid={error !== null}
            aria-describedby={error === null ? undefined : errorId}
            onChange={(event) => {
              setPassword(event.target.value)
            }}
          />
          <Button type="submit" size="sm" disabled={pending}>
            {t('users.save')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              enter('idle')
            }}
          >
            {t('users.cancel')}
          </Button>
        </form>
      )}
      {mode === 'confirming-removal' && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm">{t('users.confirmRemoval', { username: user.username })}</p>
          <Button
            ref={confirmRemoval}
            variant="destructive"
            size="sm"
            disabled={pending}
            onClick={() => {
              void run(() => removeUser(user.id), onChange, { keepPending: true })
            }}
          >
            {t('users.confirmRemove')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              enter('idle')
            }}
          >
            {t('users.cancel')}
          </Button>
        </div>
      )}
      {error !== null && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {notice !== null && (
        <p role="status" className="text-sm">
          {notice}
        </p>
      )}
    </li>
  )
}

function AddUserForm({ onAdded }: { onAdded: () => void }): ReactElement {
  const { t } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<{ message: string; field: 'username' | 'password' | null } | null>(null)

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createUser(username, password, isAdmin)
      setUsername('')
      setPassword('')
      setIsAdmin(false)
      onAdded()
    } catch (failure) {
      const code = failure instanceof ApiRequestError ? failure.code : undefined
      setError({
        message: userErrorMessage(t, failure, t('users.actionFailed')),
        field: code?.startsWith('username_') ? 'username' : code === 'password_length' ? 'password' : null,
      })
    } finally {
      setSubmitting(false)
    }
  }

  const invalid = (field: 'username' | 'password'): boolean => error?.field === field
  return (
    <form
      aria-labelledby="add-user-title"
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(event) => void submit(event)}
    >
      <h2 id="add-user-title" className="font-medium">
        {t('users.add')}
      </h2>
      <label htmlFor="add-user-username" className="text-sm font-medium">
        {t('login.username')}
      </label>
      <Input
        id="add-user-username"
        autoComplete="off"
        maxLength={64}
        autoCapitalize="none"
        spellCheck={false}
        required
        value={username}
        aria-invalid={invalid('username')}
        aria-describedby={invalid('username') ? 'add-user-error' : undefined}
        onChange={(event) => {
          setUsername(event.target.value)
        }}
      />
      <label htmlFor="add-user-password" className="text-sm font-medium">
        {t('login.password')}
      </label>
      <Input
        id="add-user-password"
        type="password"
        autoComplete="new-password"
        required
        minLength={MIN_PASSWORD_LENGTH}
        maxLength={MAX_PASSWORD_LENGTH}
        value={password}
        aria-invalid={invalid('password')}
        aria-describedby={
          invalid('password') ? 'add-user-password-hint add-user-error' : 'add-user-password-hint'
        }
        onChange={(event) => {
          setPassword(event.target.value)
        }}
      />
      <p id="add-user-password-hint" className="text-sm text-muted-foreground">
        {t('users.passwordHint', { count: MIN_PASSWORD_LENGTH })}
      </p>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={isAdmin}
          onChange={(event) => {
            setIsAdmin(event.target.checked)
          }}
        />
        {t('users.makeAdmin')}
      </label>
      {error !== null && (
        <p id="add-user-error" role="alert" className="text-sm text-destructive">
          {error.message}
        </p>
      )}
      <Button type="submit" className="self-start" disabled={submitting}>
        {t('users.addButton')}
      </Button>
    </form>
  )
}
