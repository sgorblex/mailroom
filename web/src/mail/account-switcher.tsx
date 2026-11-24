import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from '@tanstack/react-router'
import { ChevronsUpDown } from 'lucide-react'

import { AccountDot, AccountName } from '@/accounts/account-name.tsx'
import type { Account, AccountId } from '@/api/accounts.ts'
import type { ViewKey } from '@/api/mail.ts'
import { Menu } from '@/components/ui/menu.tsx'

import { MAIL_NS } from './i18n.ts'

/**
 * The account whose folders the pane lists: a button with its name and colour, opening a menu of
 * the user's accounts. Choosing one shows its inbox, or keeps the view open (`view`) while the
 * pane lists the other account's folders. With one account, its name alone.
 */
export function AccountSwitcher({
  accounts,
  accountId,
  view,
}: {
  accounts: Account[]
  accountId: AccountId
  view: ViewKey | null
}): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  // An account the URL names but the user no longer has (deleted, a stale link) is not shown
  // checked, and choosing the first one shown in its place leads to it.
  const routed = accounts.find((account) => account.id === accountId)
  const current = routed ?? accounts[0]
  if (current === undefined) {
    return null
  }
  if (accounts.length === 1) {
    return routed === undefined ? (
      <Link
        to="/mail"
        search={{ account: current.id }}
        className="flex min-w-0 rounded-md px-2 py-1 text-sm font-medium hover:bg-accent"
      >
        <AccountName account={current} />
      </Link>
    ) : (
      <p className="flex min-w-0 px-2 py-1 text-sm font-medium">
        <AccountName account={current} />
      </p>
    )
  }
  const label = t('accountSwitcher.button', { account: current.display_name })
  return (
    <>
      <button
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => {
          const target = event.currentTarget
          setAnchor((open) => (open === null ? target : null))
        }}
        className="flex min-w-0 items-center gap-2 rounded-md border px-2 py-1.5 text-left text-sm font-medium outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
      >
        <AccountName account={current} className="flex-1" />
        <ChevronsUpDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
      </button>
      {anchor !== null && (
        <Menu
          label={t('accountSwitcher.menu')}
          anchor={anchor}
          onClose={() => {
            setAnchor(null)
          }}
          items={accounts.map((account) => ({
            id: String(account.id),
            label: account.display_name,
            icon: <AccountDot colour={account.colour} />,
            checked: account.id === routed?.id,
            onSelect: () => {
              if (account.id !== accountId) {
                void navigate({ to: '/mail', search: view === null ? { account: account.id } : { account: account.id, view } })
              }
            },
          }))}
        />
      )}
    </>
  )
}
