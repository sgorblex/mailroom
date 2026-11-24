import { useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { changePassword } from '@/api/auth.ts'
import { ApiRequestError } from '@/api/http.ts'
import { useSignInWays } from '@/auth/sso.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { userErrorMessage } from '@/users/messages.ts'
import { MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH } from '@/users/password.ts'

/** Which field a failure is about, so only that one is marked invalid. */
type Field = 'current' | 'new' | 'repeat' | null

type Outcome =
  | { kind: 'idle' }
  | { kind: 'changed' }
  | { kind: 'failed'; message: string; field: Field }

/**
 * The Profile's password change: the current password, then the new one twice. `onChanged` hears of
 * one, which signed the user's other sessions out, and whether it revoked their API tokens and apps.
 * Hidden while passwords are off.
 */
export function PasswordForm({ onChanged }: {
  onChanged: (revokedAccess: boolean) => void
}): ReactElement | null {
  const { t } = useTranslation()
  const { passwords } = useSignInWays()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeated, setRepeated] = useState('')
  const [revokeAccess, setRevokeAccess] = useState(false)
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
      await changePassword(current, next, revokeAccess)
      setCurrent('')
      setNext('')
      setRepeated('')
      setRevokeAccess(false)
      setOutcome({ kind: 'changed' })
      onChanged(revokeAccess)
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

  if (passwords !== true) {
    return null
  }
  const failed = outcome.kind === 'failed'
  const invalid = (field: Field): boolean => failed && outcome.field === field
  const described = (field: Field): string | undefined =>
    invalid(field) ? 'profile-error' : undefined
  return (
    <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
      <h3 className="font-medium">{t('profile.changePassword')}</h3>
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
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={revokeAccess}
          onChange={(event) => {
            setRevokeAccess(event.target.checked)
          }}
        />
        {t('users.revokeAccess')}
      </label>
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
  )
}
