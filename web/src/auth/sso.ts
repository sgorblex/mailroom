import { useEffect, useState } from 'react'
import type { TFunction } from 'i18next'

import { fetchSsoConfig, type SsoConfig } from '@/api/sso.ts'
import { useLoad } from '@/lib/use-load.ts'

const ERROR_PARAM = 'sso_error'

/** The SSO config, or `null` while it loads or when the API does not answer it (SSO off). */
export function useSsoConfig(): SsoConfig | null {
  return useSignInWays().sso
}

/**
 * The SSO config as above, and whether passwords work: `null` while that loads, and yes when the
 * API does not answer it, which refuses them itself. Asked again when `reloadKey` changes.
 */
export function useSignInWays(reloadKey?: unknown): { sso: SsoConfig | null; passwords: boolean | null } {
  // A failure only hides the SSO controls: nothing to say about it.
  const { state } = useLoad(fetchSsoConfig, () => '', reloadKey)
  switch (state.kind) {
    case 'loading':
      return { sso: null, passwords: null }
    case 'failed':
      return { sso: null, passwords: true }
    case 'loaded':
      return { sso: state.value, passwords: state.value.password_sign_in }
  }
}

/** This page's path and query without `sso_error`: where an SSO flow comes back to. */
export function currentReturnTo(): string {
  const url = new URL(window.location.href)
  url.searchParams.delete(ERROR_PARAM)
  return `${url.pathname}${url.search}`
}

/**
 * The `sso_error` the API sent the browser back with, read once and then dropped from the
 * address bar so a reload does not show it again.
 */
export function useSsoError(): string | null {
  const [code] = useState(() => new URLSearchParams(window.location.search).get(ERROR_PARAM))
  useEffect(() => {
    if (code !== null) {
      window.history.replaceState(window.history.state, '', currentReturnTo())
    }
  }, [code])
  return code
}

/**
 * Why an SSO flow came back without signing in or linking, in the reader's language; `passwords`:
 * whether the reader can sign in with one instead.
 */
export function ssoErrorMessage(t: TFunction, code: string, passwords = true): string {
  switch (code) {
    case 'not_linked':
      return t(passwords ? 'sso.errors.notLinked' : 'sso.errors.notLinkedNoPassword')
    case 'state_invalid':
    case 'browser_mismatch':
      return t('sso.errors.stateInvalid')
    case 'denied':
      return t('sso.errors.denied')
    case 'unavailable':
    case 'not_configured':
      return t('sso.errors.unavailable')
    case 'identity_taken':
      return t('sso.errors.identityTaken')
    case 'signed_out':
      return t('sso.errors.signedOut')
    case 'busy':
      return t('sso.errors.busy')
    case 'request_gone':
      return t('connect.gone')
    default:
      return t('sso.errors.failed')
  }
}
