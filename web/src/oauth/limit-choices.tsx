import { useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { AccountChoice, AppLimits } from '@/api/oauth.ts'
import { CEILINGS, ceilingShown, toggledAccounts, type Ceiling } from '@/oauth/limit-choice.ts'

/**
 * What an app is limited to beyond its tool groups: the accounts it sees (every one, or those
 * checked), and with `sends` the most it may do when it sends.
 */
export function LimitChoices({
  accounts,
  limits,
  onChange,
  sends,
  disabled = false,
}: {
  accounts: readonly AccountChoice[]
  limits: AppLimits
  onChange: (limits: AppLimits) => void
  /** Whether the app may write or send mail at all: a ceiling limits nothing otherwise. */
  sends: boolean
  disabled?: boolean
}): ReactElement {
  const { t } = useTranslation()
  const id = useId()
  const chosen = limits.account_ids
  return (
    <>
      {accounts.length > 0 && (
        <fieldset className="flex flex-col gap-2 text-sm" disabled={disabled}>
          <legend className="mb-1 font-medium">{t('appLimits.accounts')}</legend>
          <label className="flex items-start gap-2">
            <input
              type="radio"
              name={`${id}-accounts`}
              className="mt-1"
              checked={chosen === null}
              onChange={() => {
                onChange({ ...limits, account_ids: null })
              }}
            />
            {t('appLimits.everyAccount')}
          </label>
          <label className="flex items-start gap-2">
            <input
              type="radio"
              name={`${id}-accounts`}
              className="mt-1"
              checked={chosen !== null}
              onChange={() => {
                onChange({ ...limits, account_ids: [] })
              }}
            />
            {t('appLimits.someAccounts')}
          </label>
          {chosen !== null && (
            <div className="flex flex-col gap-2 pl-6">
              {accounts.map((account) => (
                <label key={account.id} className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={chosen.includes(account.id)}
                    onChange={(event) => {
                      onChange({
                        ...limits,
                        account_ids: toggledAccounts(
                          accounts,
                          chosen,
                          account.id,
                          event.target.checked,
                        ),
                      })
                    }}
                  />
                  <span className="min-w-0 break-words">{account.name}</span>
                </label>
              ))}
              <p className="text-xs text-muted-foreground">{t('appLimits.someAccountsHint')}</p>
            </div>
          )}
        </fieldset>
      )}
      {sends && (
        <div className="flex flex-col gap-1 text-sm">
          <label htmlFor={`${id}-ceiling`} className="font-medium">
            {t('appLimits.ceiling')}
          </label>
          <select
            id={`${id}-ceiling`}
            aria-describedby={`${id}-ceiling-hint`}
            className="h-9 rounded-md border bg-transparent px-2"
            disabled={disabled}
            value={ceilingShown(limits.send_ceiling) ?? ''}
            onChange={(event) => {
              const value = event.target.value
              onChange({ ...limits, send_ceiling: value === '' ? null : (value as Ceiling) })
            }}
          >
            <option value="">{t('appLimits.noCeiling')}</option>
            {CEILINGS.map((mode) => (
              <option key={mode} value={mode}>
                {t(`accounts.sendModes.${mode}`)}
              </option>
            ))}
          </select>
          <p id={`${id}-ceiling-hint`} className="text-xs text-muted-foreground">
            {t('appLimits.ceilingHint')}
          </p>
        </div>
      )}
    </>
  )
}
