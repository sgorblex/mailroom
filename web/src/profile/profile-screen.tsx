import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { changePassword } from '@/api/auth.ts'
import { ApiRequestError } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { LinkedSignIn } from '@/profile/linked-sign-in.tsx'
import { userErrorMessage } from '@/users/messages.ts'
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from '@/users/password.ts'

/** Which field a failure is about, so only that one is marked invalid. */
type Field = 'current' | 'new' | 'repeat' | null

type Outcome =
  | { kind: 'idle' }
  | { kind: 'changed' }
  | { kind: 'failed'; message: string; field: Field }

/** The signed-in user's own page: their password and linked sign-ins. */
export function ProfileScreen(): ReactElement {
  const { t } = useTranslation()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeated, setRepeated] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (next !== repeated) {
      setOutcome({ kind: 'failed', message: t('profile.passwordsDiffer'), field: 'repeat' })
      return
    }
    setSubmitting(true)
    setOutcome({ kind: 'idle' })
    try {
      await changePassword(current, next)
      setCurrent('')
      setNext('')
      setRepeated('')
      setOutcome({ kind: 'changed' })
    } catch (failure) {
      const code = failure instanceof ApiRequestError ? failure.code : undefined
      setOutcome({
        kind: 'failed',
        message: userErrorMessage(t, failure, t('profile.unknownError')),
        field: code === 'wrong_password' ? 'current' : code === 'password_length' ? 'new' : null,
      })
    } finally {
      setSubmitting(false)
    }
  }

  const failed = outcome.kind === 'failed'
  const invalid = (field: Field): boolean => failed && outcome.field === field
  const described = (field: Field): string | undefined =>
    invalid(field) ? 'profile-error' : undefined
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('profile.title')}</h1>
      <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        <h2 className="font-medium">{t('profile.changePassword')}</h2>
        <label htmlFor="profile-current" className="text-sm font-medium">
          {t('profile.currentPassword')}
        </label>
        <Input
          id="profile-current"
          type="password"
          autoComplete="current-password"
          required
          maxLength={MAX_PASSWORD_LENGTH}
          value={current}
          aria-invalid={invalid('current')}
          aria-describedby={described('current')}
          onChange={(event) => {
            setCurrent(event.target.value)
          }}
        />
        <label htmlFor="profile-new" className="text-sm font-medium">
          {t('profile.newPassword')}
        </label>
        <Input
          id="profile-new"
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          maxLength={MAX_PASSWORD_LENGTH}
          value={next}
          aria-invalid={invalid('new')}
          aria-describedby={invalid('new') ? 'profile-new-hint profile-error' : 'profile-new-hint'}
          onChange={(event) => {
            setNext(event.target.value)
          }}
        />
        <p id="profile-new-hint" className="text-sm text-muted-foreground">
          {t('users.passwordHint', { count: MIN_PASSWORD_LENGTH })}
        </p>
        <label htmlFor="profile-repeat" className="text-sm font-medium">
          {t('profile.repeatPassword')}
        </label>
        <Input
          id="profile-repeat"
          type="password"
          autoComplete="new-password"
          required
          maxLength={MAX_PASSWORD_LENGTH}
          value={repeated}
          aria-invalid={invalid('repeat')}
          aria-describedby={described('repeat')}
          onChange={(event) => {
            setRepeated(event.target.value)
          }}
        />
        {failed && (
          <p id="profile-error" role="alert" className="text-sm text-destructive">
            {outcome.message}
          </p>
        )}
        {outcome.kind === 'changed' && (
          <p role="status" className="text-sm">
            {t('profile.changed')}
          </p>
        )}
        <Button type="submit" disabled={submitting}>
          {t('profile.save')}
        </Button>
      </form>
      <LinkedSignIn />
    </main>
  )
}
