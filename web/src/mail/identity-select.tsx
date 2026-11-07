import { useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { Account, Identity } from '@/api/accounts.ts'

import { addressFull } from './format.ts'
import { MAIL_NS } from './i18n.ts'

function defaultIdentity(identities: Identity[]): Identity | undefined {
  return identities.find((identity) => identity.default === true) ?? identities[0]
}

function label(identity: Identity): string {
  return addressFull({ name: identity.name ?? '', address: identity.address })
}

/** The composer's `From`: a choice once the account has several identities. */
export function IdentitySelect({
  account,
  value,
  onChange,
}: {
  account: Account | undefined
  value: string | null
  onChange: (address: string) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const id = useId()
  const identities = account?.identities ?? []
  const fallback = defaultIdentity(identities)
  if (identities.length < 2) {
    return (
      <p className="flex flex-col gap-1 text-sm">
        <span className="text-muted-foreground">{t('message.from')}</span>
        <span className="break-words">{fallback === undefined ? account?.login : label(fallback)}</span>
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm text-muted-foreground">
        {t('message.from')}
      </label>
      <select
        id={id}
        className="h-9 min-w-0 rounded-md border bg-background px-2 text-sm text-foreground"
        value={value ?? fallback?.address}
        onChange={(event) => {
          onChange(event.target.value)
        }}
      >
        {identities.map((identity) => (
          <option key={identity.address} value={identity.address}>
            {label(identity)}
          </option>
        ))}
      </select>
    </div>
  )
}
