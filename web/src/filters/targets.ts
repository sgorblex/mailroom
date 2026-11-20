import { createContext } from 'react'

import type { Account } from '@/api/accounts.ts'

/** The accounts a filter can move mail to (`move_to_account`): the user's others; none until given. */
export const TargetAccountsContext = createContext<readonly Account[]>([])
