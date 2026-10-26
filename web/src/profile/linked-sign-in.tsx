import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { apiErrorMessage } from '@/api/http.ts'
import { listIdentities, ssoStartUrl, unlinkIdentity, type Identity } from '@/api/sso.ts'
import { currentReturnTo, ssoErrorMessage, useSsoConfig, useSsoError } from '@/auth/sso.ts'
import { Button } from '@/components/ui/button.tsx'
import { buttonVariants } from '@/components/ui/button-variants.ts'
import { useLoad } from '@/lib/use-load.ts'

/**
 * The user's linked sign-ins: each lets them sign in through the identity provider. Hidden while
 * SSO is off and nothing is linked.
 */
export function LinkedSignIn(): ReactElement | null {
  const { t } = useTranslation()
  const sso = useSsoConfig()
  const ssoError = useSsoError()
  const [reloadKey, setReloadKey] = useState(0)
  const [failure, setFailure] = useState<string | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const load = useCallback((signal: AbortSignal) => listIdentities(signal), [])
  const { state } = useLoad(
    load,
    (error) => apiErrorMessage(error, t('sso.loadFailed')),
    reloadKey,
  )

  async function unlink(identity: Identity): Promise<void> {
    setPending(identity.id)
    setFailure(null)
    try {
      await unlinkIdentity(identity.id)
      setReloadKey((value) => value + 1)
    } catch (error) {
      setFailure(apiErrorMessage(error, t('sso.unlinkFailed')))
    } finally {
      setPending(null)
    }
  }

  const name = sso?.enabled === true ? sso.name : null
  const identities = state.kind === 'loaded' ? state.value : []
  if (name === null && identities.length === 0 && ssoError === null) {
    return null
  }
  const linkedHere = identities.some((identity) => identity.name !== null)
  return (
    <section aria-labelledby="linked-sign-in" className="flex flex-col gap-3">
      <h2 id="linked-sign-in" className="font-medium">
        {t('sso.linkedTitle')}
      </h2>
      {ssoError !== null && (
        <p role="alert" className="text-sm text-destructive">
          {ssoErrorMessage(t, ssoError)}
        </p>
      )}
      {state.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {state.message}
        </p>
      )}
      {state.kind === 'loaded' && identities.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('sso.noneLinked')}</p>
      )}
      {identities.length > 0 && (
        <ul aria-label={t('sso.linkedTitle')} className="flex flex-col divide-y rounded-lg border">
          {identities.map((identity) => {
            const label = identity.name ?? identity.issuer
            return (
              <li key={identity.id} className="flex items-center gap-3 p-3">
                <div className="mr-auto min-w-0">
                  <p className="truncate text-sm font-medium">{label}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {t('sso.subject', { subject: identity.subject })}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t('sso.unlinkFrom', { name: label })}
                  disabled={pending !== null}
                  onClick={() => void unlink(identity)}
                >
                  {t('sso.unlink')}
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {name !== null && !linkedHere && (
        <a
          className={buttonVariants({ variant: 'outline', className: 'self-start' })}
          href={ssoStartUrl('link', currentReturnTo())}
        >
          {t('sso.link', { name })}
        </a>
      )}
    </section>
  )
}
