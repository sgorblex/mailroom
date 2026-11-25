import { useCallback, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'

import { apiErrorMessage } from '@/api/http.ts'
import {
  listConnectedApps,
  revokeConnectedApp,
  setConnectedAppGroups,
  setConnectedAppLimits,
  type AccountChoice,
  type AppLimits,
  type ConnectedApp,
  type ToolGroup,
} from '@/api/oauth.ts'
import { useTimeZone } from '@/auth/auth-context.ts'
import { ConfirmRemoval } from '@/components/confirm-removal.tsx'
import { Button } from '@/components/ui/button.tsx'
import { formatDateTime } from '@/lib/format.ts'
import { useLoad } from '@/lib/use-load.ts'
import { everyApprovable, groupLabel } from '@/oauth/group-choice.ts'
import { GroupChoices } from '@/oauth/group-choices.tsx'
import { anySends, ceilingShown, limitsApprovable } from '@/oauth/limit-choice.ts'
import { LimitChoices } from '@/oauth/limit-choices.tsx'

/**
 * The apps (MCP clients) the user approved, with the tool groups each may use and its limits, which
 * the user changes here, and each revocable after a confirm. With an external issuer the identity provider
 * manages access instead; with the MCP server off the section shows only leftovers, and nothing
 * once it knows there are none.
 */
export function ConnectedApps(): ReactElement | null {
  const { t, i18n } = useTranslation()
  const timeZone = useTimeZone()
  const [reloadKey, setReloadKey] = useState(0)
  const [failure, setFailure] = useState<string | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)
  // The app whose tool groups are being changed, and the choice so far.
  const [editing, setEditing] = useState<{ id: number; chosen: string[] } | null>(null)
  // The app whose limits are being changed, and the choice so far.
  const [limiting, setLimiting] = useState<{ id: number; limits: AppLimits } | null>(null)
  // Each row's Disconnect button, to give focus back to when its confirm is cancelled.
  const revokeTriggers = useRef(new Map<number, HTMLButtonElement>())
  const load = useCallback((signal: AbortSignal) => listConnectedApps(signal), [])
  const { state } = useLoad(
    load,
    (error) => apiErrorMessage(error, t('connectedApps.loadFailed')),
    reloadKey,
  )

  async function revoke(app: ConnectedApp): Promise<void> {
    setPending(app.id)
    setFailure(null)
    try {
      await revokeConnectedApp(app.id)
    } catch (error) {
      setFailure(apiErrorMessage(error, t('connectedApps.revokeFailed')))
    } finally {
      setPending(null)
      setConfirming(null)
      // Gone either way when it was already revoked elsewhere: show what is left.
      setReloadKey((value) => value + 1)
    }
  }

  async function saveGroups(app: ConnectedApp, groups: string[]): Promise<void> {
    setPending(app.id)
    setFailure(null)
    try {
      await setConnectedAppGroups(app.id, groups)
      setEditing(null)
    } catch (error) {
      setFailure(apiErrorMessage(error, t('connectedApps.groupsFailed')))
    } finally {
      setPending(null)
      setReloadKey((value) => value + 1)
    }
  }

  async function saveLimits(app: ConnectedApp, limits: AppLimits): Promise<void> {
    setPending(app.id)
    setFailure(null)
    try {
      await setConnectedAppLimits(app.id, limits)
      setLimiting(null)
    } catch (error) {
      setFailure(apiErrorMessage(error, t('connectedApps.limitsFailed')))
    } finally {
      setPending(null)
      setReloadKey((value) => value + 1)
    }
  }

  if (state.kind === 'loading') {
    // A line while it asks, so the card (and its group) is not taken for one with nothing in it.
    return (
      <section aria-labelledby="connected-apps" className="flex flex-col gap-3">
        <h3 id="connected-apps" className="font-medium">
          {t('connectedApps.title')}
        </h3>
        <p className="text-sm text-muted-foreground">{t('connectedApps.loading')}</p>
      </section>
    )
  }
  const auth = state.kind === 'loaded' ? state.value.auth : null
  const grants = state.kind === 'loaded' ? state.value.grants : []
  const served = state.kind === 'loaded' ? state.value.groups : []
  const accounts = state.kind === 'loaded' ? state.value.accounts : []
  if (auth === 'off' && grants.length === 0) {
    return null
  }
  return (
    <section aria-labelledby="connected-apps" className="flex flex-col gap-3">
      <h3 id="connected-apps" className="font-medium">
        {t('connectedApps.title')}
      </h3>
      {state.kind === 'failed' && <p role="alert">{state.message}</p>}
      {auth === 'external' && (
        <p className="text-sm text-muted-foreground">
          {t('connectedApps.external')}
          {grants.length > 0 && ` ${t('connectedApps.externalLeftovers')}`}
        </p>
      )}
      {(auth === 'builtin' || auth === 'oidc') && grants.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('connectedApps.none')}</p>
      )}
      {grants.length > 0 && (
        <ul aria-label={t('connectedApps.title')} className="flex flex-col divide-y rounded-lg border">
          {grants.map((app) => {
            const name = app.client_name ?? t('connectedApps.unnamed')
            return (
              <li key={app.id} className="flex flex-col gap-2 p-3">
                <div className="flex items-center gap-3">
                  <div className="mr-auto min-w-0">
                    <p className="truncate text-sm font-medium">{name}</p>
                    {app.client_host !== null && (
                      <p className="truncate text-xs text-muted-foreground">{app.client_host}</p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {t('connectedApps.connected', {
                        when: formatDateTime(i18n.language, app.created_at, timeZone),
                      })}
                      {' · '}
                      {app.last_used_at === null
                        ? t('connectedApps.neverUsed')
                        : t('connectedApps.lastUsed', {
                            when: formatDateTime(i18n.language, app.last_used_at, timeZone),
                          })}
                    </p>
                  </div>
                  {confirming !== app.id && (
                    <Button
                      ref={(button) => {
                        if (button === null) {
                          revokeTriggers.current.delete(app.id)
                        } else {
                          revokeTriggers.current.set(app.id, button)
                        }
                      }}
                      variant="outline"
                      size="sm"
                      aria-label={t('connectedApps.revokeApp', { name })}
                      disabled={pending !== null}
                      onClick={() => {
                        setConfirming(app.id)
                        setFailure(null)
                      }}
                    >
                      {t('connectedApps.revoke')}
                    </Button>
                  )}
                </div>
                {served.length > 0 && editing?.id !== app.id && (
                  <AppGroups
                    app={app}
                    served={served}
                    pending={pending !== null}
                    onApprove={(groups) => void saveGroups(app, groups)}
                    onChange={() => {
                      setEditing({ id: app.id, chosen: app.groups })
                      setFailure(null)
                    }}
                  />
                )}
                {editing?.id === app.id && (
                  <div className="flex flex-col gap-3 rounded-md border p-3">
                    <GroupChoices
                      groups={served}
                      chosen={editing.chosen}
                      onChange={(chosen) => {
                        setEditing({ id: app.id, chosen })
                      }}
                      disabled={pending !== null}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={pending !== null || editing.chosen.length === 0}
                        onClick={() => void saveGroups(app, editing.chosen)}
                      >
                        {t('connectedApps.saveGroups')}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={pending !== null}
                        onClick={() => {
                          setEditing(null)
                        }}
                      >
                        {t('connectedApps.cancel')}
                      </Button>
                    </div>
                  </div>
                )}
                {served.length > 0 && limiting?.id !== app.id && (
                  <AppLimitsSummary
                    app={app}
                    accounts={accounts}
                    pending={pending !== null}
                    onChange={() => {
                      const { account_ids, send_ceiling } = app
                      setLimiting({ id: app.id, limits: { account_ids, send_ceiling } })
                      setFailure(null)
                    }}
                  />
                )}
                {limiting?.id === app.id && (
                  <div className="flex flex-col gap-3 rounded-md border p-3">
                    <LimitChoices
                      accounts={accounts}
                      limits={limiting.limits}
                      onChange={(limits) => {
                        setLimiting({ id: app.id, limits })
                      }}
                      sends={anySends(served.map((group) => group.name))}
                      disabled={pending !== null}
                    />
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        disabled={pending !== null || !limitsApprovable(limiting.limits)}
                        onClick={() => void saveLimits(app, limiting.limits)}
                      >
                        {t('connectedApps.saveLimits')}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={pending !== null}
                        onClick={() => {
                          setLimiting(null)
                        }}
                      >
                        {t('connectedApps.cancel')}
                      </Button>
                    </div>
                  </div>
                )}
                <Link
                  to="/activity"
                  search={{ app: `grant:${String(app.id)}` }}
                  aria-label={t('connectedApps.activityNamed', { name })}
                  className="self-start text-xs underline underline-offset-4"
                >
                  {t('connectedApps.activity')}
                </Link>
                {confirming === app.id && (
                  <ConfirmRemoval
                    question={t('connectedApps.confirmRevoke', { name })}
                    confirmLabel={t('connectedApps.confirm')}
                    cancelLabel={t('connectedApps.cancel')}
                    pending={pending !== null}
                    onConfirm={() => void revoke(app)}
                    onCancel={() => {
                      setConfirming(null)
                      requestAnimationFrame(() => revokeTriggers.current.get(app.id)?.focus())
                    }}
                  />
                )}
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
    </section>
  )
}

/** An app's tool groups, those it is not approved for, and the buttons to change them. */
function AppGroups({
  app,
  served,
  pending,
  onApprove,
  onChange,
}: {
  app: ConnectedApp
  served: readonly ToolGroup[]
  pending: boolean
  onApprove: (groups: string[]) => void
  onChange: () => void
}): ReactElement {
  const { t } = useTranslation()
  const labels = (names: readonly string[]): string =>
    names.map((name) => groupLabel(t, name)).join(', ')
  const missing = served.map((group) => group.name).filter((name) => !app.groups.includes(name))
  const approvable = everyApprovable(served)
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p>
        {app.groups.length === 0
          ? t('connectedApps.noGroups')
          : t('connectedApps.groups', { groups: labels(app.groups) })}
      </p>
      {missing.length > 0 && (
        <p className="text-muted-foreground">
          {t('connectedApps.notApproved', { groups: labels(missing) })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {missing.some((name) => approvable.includes(name)) && (
          <Button
            size="sm"
            variant="outline"
            disabled={pending}
            onClick={() => {
              onApprove(approvable)
            }}
          >
            {t('connectedApps.approveAll')}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={pending} onClick={onChange}>
          {t('connectedApps.changeGroups')}
        </Button>
      </div>
    </div>
  )
}

/** An app's limits in words, and the button to change them. */
function AppLimitsSummary({
  app,
  accounts,
  pending,
  onChange,
}: {
  app: ConnectedApp
  accounts: readonly AccountChoice[]
  pending: boolean
  onChange: () => void
}): ReactElement {
  const { t } = useTranslation()
  const ids = app.account_ids
  const names = accounts.filter((account) => ids?.includes(account.id)).map(({ name }) => name)
  const ceiling = ceilingShown(app.send_ceiling)
  return (
    <div className="flex flex-col gap-2 text-xs">
      <p className="break-words">
        {ids === null
          ? t('connectedApps.everyAccount')
          : names.length === 0
            ? t('connectedApps.noAccounts')
            : t('connectedApps.accounts', { accounts: names.join(', ') })}
      </p>
      {ceiling !== null && (
        <p>{t('connectedApps.ceiling', { mode: t(`accounts.sendModes.${ceiling}`) })}</p>
      )}
      <Button
        className="self-start"
        size="sm"
        variant="outline"
        disabled={pending}
        onClick={onChange}
      >
        {t('connectedApps.changeLimits')}
      </Button>
    </div>
  )
}
