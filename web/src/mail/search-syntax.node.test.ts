import { describe, expect, it } from 'vitest'

import type { Account } from '@/api/accounts.ts'
import vectors from '@/test/search-syntax-vectors.json'

import { accountTerm, lineOf, namedAccount, parseSearch, terms, withField, withoutTerm, type ParsedSearch } from './search-syntax.ts'

function account(id: number, name: string | null, login: string): Account {
  return { id, name, login } as Account
}

const ACCOUNTS = [account(1, 'Work', 'me@work.example'), account(2, null, 'me@home.example'), account(3, 'Twin', 'a@x'), account(4, 'twin', 'b@x')]

describe('the search syntax', () => {
  const read = vectors.flatMap((vector) => ('parsed' in vector ? [[vector.input, vector.parsed] as const] : []))
  const refused = vectors.flatMap((vector) => ('error' in vector ? [[vector.input, vector.error] as const] : []))

  it.each(read)('reads %j as the backend does', (input, parsed) => {
    expect(parseSearch(input)).toEqual({ ok: true, parsed })
  })

  it.each(refused)('refuses %j as the backend does, with %s', (input, code) => {
    expect(parseSearch(input)).toMatchObject({ ok: false, code })
  })

  it('writes a search as a line that reads back the same', () => {
    const parsed: ParsedSearch = {
      text: 'report from:x',
      from: 'Ada Lovelace',
      subject: 'a"b',
      unread: false,
      to_sort: true,
      has_attachment: true,
      since: '2026-01-01',
      folder: 'Old Projects',
      account: '*',
    }
    const line = lineOf(parsed)
    expect(line).toBe('report "from:x" from:"Ada Lovelace" subject:ab is:read is:to_sort has:attachment after:2026-01-01 in:"Old Projects" account:*')
    expect(parseSearch(line)).toEqual({ ok: true, parsed: { ...parsed, subject: 'ab' } })
  })

  it('sets a criterion in place, drops its other terms and adds a new one at the end', () => {
    expect(withField('invoice from:ada is:read', 'from', 'Ada L')).toBe('invoice from:"Ada L" is:read')
    expect(withField('from:a invoice from:a', 'from', 'bob')).toBe('from:bob invoice')
    expect(withField('is:read invoice', 'unread', true)).toBe('is:unread invoice')
    expect(withField('invoice is:unread  more', 'unread', undefined)).toBe('invoice more')
    expect(withField('invoice', 'folder', 'INBOX')).toBe('invoice in:INBOX')
    expect(withField('', 'since', '2026-03-01')).toBe('after:2026-03-01')
    expect(withField('"open', 'from', 'ada')).toBeNull()
  })

  it('rewrites or drops a term at fault whose key names the criterion', () => {
    expect(withField('after:2026-13-01', 'since', '2026-03-01')).toBe('after:2026-03-01')
    expect(withField('invoice from:', 'from', 'ada')).toBe('invoice from:ada')
    expect(withField('is:starred invoice', 'flagged', true)).toBe('is:flagged invoice')
    expect(withField('invoice is:', 'unread', undefined)).toBe('invoice')
    expect(withField('has:pdf', 'has_attachment', true)).toBe('has:attachment')
  })

  it('drops a term with the spaces after it', () => {
    const line = 'from:ada  "board meeting" is:unread'
    const [from, words, unread] = terms(line).terms
    expect(from && withoutTerm(line, from)).toBe('"board meeting" is:unread')
    expect(words && withoutTerm(line, words)).toBe('from:ada  is:unread')
    expect(unread && withoutTerm(line, unread)).toBe('from:ada  "board meeting"')
  })

  it('finds the account account: names as the backend does', () => {
    expect(namedAccount(ACCOUNTS, '*')).toEqual({ ok: true, account: '*' })
    expect(namedAccount(ACCOUNTS, '2')).toEqual({ ok: true, account: '2' })
    expect(namedAccount(ACCOUNTS, 'WORK')).toEqual({ ok: true, account: '1' })
    expect(namedAccount(ACCOUNTS, 'me@home.example')).toEqual({ ok: true, account: '2' })
    expect(namedAccount(ACCOUNTS, '9')).toEqual({ ok: false, code: 'unknown_account' })
    expect(namedAccount(ACCOUNTS, 'nobody')).toEqual({ ok: false, code: 'unknown_account' })
    expect(namedAccount(ACCOUNTS, 'twin')).toEqual({ ok: false, code: 'ambiguous_account' })
  })

  it('names an account by its name, else its login, else its id', () => {
    expect(accountTerm(ACCOUNTS, '1')).toBe('Work')
    expect(accountTerm(ACCOUNTS, '2')).toBe('me@home.example')
    expect(accountTerm(ACCOUNTS, '3')).toBe('a@x')
    expect(accountTerm(ACCOUNTS, '*')).toBe('*')
  })
})
