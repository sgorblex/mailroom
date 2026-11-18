import { describe, expect, it } from 'vitest'

import { validateMailSearch } from './route-search.ts'

describe('the mail URL', () => {
  it('takes the account as its id, a positive integer, whether the router parsed it or not', () => {
    expect(validateMailSearch({ account: 3 })).toEqual({ account: 3 })
    expect(validateMailSearch({ account: '12' })).toEqual({ account: 12 })
  })

  it('drops an account that is no id, as a link from before ids were numbers has', () => {
    for (const account of ['work', '0', '-1', '1.5', 0, 1.5, 2 ** 53]) {
      expect(validateMailSearch({ account, folder: 'INBOX' })).toEqual({ folder: 'INBOX' })
    }
  })

  it("keeps a search's account only as an id or every account", () => {
    expect(validateMailSearch({ q: { account: '3', from: 'ana' } })).toEqual({ q: { account: '3', from: 'ana' } })
    expect(validateMailSearch({ q: { account: 3 } })).toEqual({ q: { account: '3' } })
    expect(validateMailSearch({ q: { account: '*' } })).toEqual({ q: { account: '*' } })
    expect(validateMailSearch({ q: { account: 'work', from: 'ana' } })).toEqual({ q: { from: 'ana' } })
  })
})
