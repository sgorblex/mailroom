import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiRequestError, apiErrorMessage } from '@/api/http.ts'
import { useAuth } from '@/auth/auth-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

/** Shown in place of any page while signed out, so the URL survives signing in. */
export function LoginScreen(): ReactElement {
  const { t } = useTranslation()
  const auth = useAuth()
  const ended = auth.status === 'signed-out' && auth.ended === true
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function failureMessage(failure: unknown): string {
    if (failure instanceof ApiRequestError && failure.status === 401) {
      return t('login.wrongCredentials')
    }
    if (failure instanceof ApiRequestError && failure.status === 429) {
      return t('login.tooManyAttempts')
    }
    return apiErrorMessage(failure, t('login.unknownError'))
  }

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await auth.signIn(username, password)
    } catch (failure) {
      setError(failureMessage(failure))
      setSubmitting(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('appName')}</h1>
      {ended && <p role="status">{t('login.sessionEnded')}</p>}
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <label htmlFor="login-username" className="text-sm font-medium">
          {t('login.username')}
        </label>
        <Input
          id="login-username"
          autoComplete="username"
          maxLength={64}
          autoCapitalize="none"
          spellCheck={false}
          required
          value={username}
          aria-invalid={error !== null}
          aria-describedby={error === null ? undefined : 'login-error'}
          onChange={(event) => {
            setUsername(event.target.value)
          }}
        />
        <label htmlFor="login-password" className="text-sm font-medium">
          {t('login.password')}
        </label>
        <Input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          aria-invalid={error !== null}
          aria-describedby={error === null ? undefined : 'login-error'}
          onChange={(event) => {
            setPassword(event.target.value)
          }}
        />
        {error !== null && (
          <p id="login-error" role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <Button type="submit" disabled={submitting || username.trim() === '' || password === ''}>
          {t('login.signIn')}
        </Button>
      </form>
    </main>
  )
}
