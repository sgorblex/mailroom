import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'

import { listApiTokens } from '@/api/api-tokens.ts'
import { listConnectedApps, type ConnectedApp } from '@/api/oauth.ts'
import { useAuth, useTimeZone } from '@/auth/auth-context.ts'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'

import type { AppKey } from './activity-search.ts'
import { ACTIVITY_NS } from './i18n.ts'

interface AppChoice {
  key: AppKey
  name: string
}

/** `null` for a list that did not load. */
interface Known {
  grants: ConnectedApp[] | null
  tokens: { id: number; name: string }[] | null
}

/** What `asked` answers, `null` when it fails: the filter then names apps less, and the list still loads. */
async function orNull<T>(asked: Promise<T>): Promise<T | null> {
  try {
    return await asked
  } catch {
    return null
  }
}

/**
 * The apps the list can be narrowed to: the user's connected apps and API tokens, and `chosen`
 * when it is neither (revoked since, or a client of an external issuer).
 */
export function useAppChoices(chosen: AppKey | undefined): AppChoice[] {
  const { t, i18n } = useTranslation([ACTIVITY_NS, 'common'])
  const timeZone = useTimeZone()
  const mcp = useAuth().features.has('mcp')
  const load = useCallback(
    async (signal: AbortSignal): Promise<Known> => {
      const [apps, tokens] = await Promise.all([
        // Not there with MCP off.
        mcp ? orNull(listConnectedApps(signal)) : null,
        orNull(listApiTokens(signal)),
      ])
      return { grants: mcp ? (apps?.grants ?? null) : [], tokens: tokens?.tokens ?? null }
    },
    [mcp],
  )
  const { state } = useLoad(load, () => '')
  const known: Known = state.kind === 'loaded' ? state.value : { grants: null, tokens: null }

  const appName = (app: ConnectedApp): string => app.client_name ?? t('common:connectedApps.unnamed')
  const names = [...(known.grants ?? []).map(appName), ...(known.tokens ?? []).map((token) => token.name)]
  const repeated = (name: string): boolean => names.filter((each) => each === name).length > 1
  const choices: AppChoice[] = [
    ...(known.grants ?? []).map((app): AppChoice => {
      const name = appName(app)
      const when = formatDateTime(i18n.language, app.created_at, timeZone)
      return { key: `grant:${String(app.id)}`, name: repeated(name) ? t('app.connected', { name, when }) : name }
    }),
    ...(known.tokens ?? []).map(
      ({ id, name }): AppChoice => ({ key: `token:${String(id)}`, name: t('app.token', { name }) }),
    ),
  ]
  if (chosen === undefined || choices.some(({ key }) => key === chosen)) {
    return choices
  }
  const unlisted = (): string => {
    if (chosen.startsWith('client:')) {
      return chosen.slice('client:'.length)
    }
    const [list, gone] = chosen.startsWith('token:')
      ? [known.tokens, t('who.agentToken')]
      : [known.grants, t('app.disconnected')]
    return list === null ? t('filters.chosen') : gone
  }
  return [...choices, { key: chosen, name: unlisted() }]
}
