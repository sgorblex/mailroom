import type { ReactElement } from 'react'

import type { AccountColour } from '@/api/accounts.ts'
import { cn } from '@/lib/utils.ts'

import type { AccountLabel } from './accounts-context.ts'

/** Each colour as a dot: dark enough on the light theme, light enough on the dark one (3:1). */
const DOT: Record<AccountColour, string> = {
  slate: 'bg-slate-600 dark:bg-slate-400',
  red: 'bg-red-600 dark:bg-red-400',
  orange: 'bg-orange-600 dark:bg-orange-400',
  amber: 'bg-amber-600 dark:bg-amber-400',
  green: 'bg-green-600 dark:bg-green-400',
  teal: 'bg-teal-600 dark:bg-teal-400',
  sky: 'bg-sky-600 dark:bg-sky-400',
  blue: 'bg-blue-600 dark:bg-blue-400',
  violet: 'bg-violet-600 dark:bg-violet-400',
  pink: 'bg-pink-600 dark:bg-pink-400',
}

/** The account's colour as a small dot beside its name; nothing without a colour. */
export function AccountDot({ colour, className }: { colour: AccountColour | null; className?: string }): ReactElement | null {
  if (colour === null) {
    return null
  }
  return <span aria-hidden="true" className={cn('inline-block size-2 shrink-0 rounded-full', DOT[colour], className)} />
}

/** An account as the web shows it: its colour dot and its name. */
export function AccountName({ account, className }: { account: AccountLabel; className?: string }): ReactElement {
  const { display_name: name, colour } = account
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)}>
      <AccountDot colour={colour} />
      <span className="truncate">{name}</span>
    </span>
  )
}
