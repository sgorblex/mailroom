import type { Account } from '@/api/accounts.ts'

/**
 * The search syntax: one line such as `from:ada is:unread "board meeting"` as a search's criteria,
 * folder and account (`docs/requirements/mail-client.md`, *Search syntax*). The backend parses the
 * same grammar (`mailroom.mail.search_syntax`); both are held to `@/test/search-syntax-vectors.json`.
 */

export type SyntaxCode = 'unterminated_quote' | 'empty_value' | 'bad_value' | 'bad_date' | 'conflict'

/** What a query asks for, named as `POST /search` names it; absent is not asked. */
export interface ParsedSearch {
  from?: string
  to?: string
  subject?: string
  text?: string
  since?: string
  before?: string
  unread?: boolean
  flagged?: boolean
  has_attachment?: boolean
  to_sort?: boolean
  folder?: string
  account?: string
}

export type SearchField = keyof ParsedSearch

/** One term of a line, where it is (`start`, `end`), its key (lowercased) when the syntax knows it, its value unquoted. */
export interface Term {
  start: number
  end: number
  key: string | null
  value: string
}

export type ParseResult = { ok: true; parsed: ParsedSearch } | { ok: false; code: SyntaxCode; key?: string }

const SPACE = new Set([' ', '\t', '\n', '\r', '\f', '\v'])
const KEY = /([A-Za-z_]+):/y
const DATE = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/
const FIELD = { from: 'from', to: 'to', subject: 'subject', in: 'folder', account: 'account' } as const
const DATES = { after: 'since', before: 'before' } as const
const FLAGS = {
  is: {
    unread: ['unread', true],
    read: ['unread', false],
    flagged: ['flagged', true],
    to_sort: ['to_sort', true],
  },
  has: { attachment: ['has_attachment', true] },
} as const satisfies Record<string, Record<string, readonly [SearchField, boolean]>>

type FieldKey = keyof typeof FIELD
type DateKey = keyof typeof DATES
type FlagKey = keyof typeof FLAGS

const isField = (key: string): key is FieldKey => Object.hasOwn(FIELD, key)
const isDate = (key: string): key is DateKey => Object.hasOwn(DATES, key)
const isFlag = (key: string): key is FlagKey => Object.hasOwn(FLAGS, key)

/** The keys of `is:` and `has:` a value names, in the order the syntax lists them. */
export function flagValues(key: string): string[] {
  return isFlag(key) ? Object.keys(FLAGS[key]) : []
}

/** The value from `start` up to the next space outside quotes, its quotes dropped, and where it ends; `null` when a quote is not closed. */
function value(line: string, start: number): [string, number] | null {
  let out = ''
  let i = start
  while (i < line.length && !SPACE.has(line.charAt(i))) {
    if (line.charAt(i) === '"') {
      const end = line.indexOf('"', i + 1)
      if (end < 0) {
        return null
      }
      out += line.slice(i + 1, end)
      i = end + 1
    } else {
      out += line.charAt(i)
      i += 1
    }
  }
  return [out, i]
}

/** The line's terms, and whether a quote is left open after them (no term follows one). */
export function terms(line: string): { terms: Term[]; unterminated: boolean } {
  const out: Term[] = []
  let i = 0
  while (i < line.length) {
    if (SPACE.has(line.charAt(i))) {
      i += 1
      continue
    }
    KEY.lastIndex = i
    const match = KEY.exec(line)
    const key = match?.[1]?.toLowerCase() ?? ''
    const known = match !== null && (isField(key) || isDate(key) || isFlag(key))
    const read = value(line, known ? KEY.lastIndex : i)
    if (read === null) {
      return { terms: out, unterminated: true }
    }
    out.push({ start: i, end: read[1], key: known ? key : null, value: read[0] })
    i = read[1]
  }
  return { terms: out, unterminated: false }
}

function realDate(text: string): boolean {
  const match = DATE.exec(text)
  if (match === null) {
    return false
  }
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
  return year >= 1 && days !== undefined && day >= 1 && day <= days
}

type Meaning = { field: SearchField; value: string | boolean } | { code: Exclude<SyntaxCode, 'conflict' | 'unterminated_quote'> }

/** What one term sets, or why it cannot. */
function meaning(term: Term): Meaning {
  const { key } = term
  if (key === null) {
    return { field: 'text', value: term.value }
  }
  if (term.value === '') {
    return { code: 'empty_value' }
  }
  if (isField(key)) {
    return { field: FIELD[key], value: term.value }
  }
  if (isDate(key)) {
    return realDate(term.value) ? { field: DATES[key], value: term.value } : { code: 'bad_date' }
  }
  if (isFlag(key)) {
    const flags: Record<string, readonly [SearchField, boolean]> = FLAGS[key]
    const flag = Object.hasOwn(flags, term.value.toLowerCase()) ? flags[term.value.toLowerCase()] : undefined
    return flag === undefined ? { code: 'bad_value' } : { field: flag[0], value: flag[1] }
  }
  return { field: 'text', value: term.value }
}

/** The criterion a term sets: `null` for one that sets none (`is:starred`, `from:`). */
export function termField(term: Term): SearchField | null {
  const read = meaning(term)
  return 'field' in read ? read.field : null
}

/** The criteria a term's key names: the one it sets, else, for a term at fault, every one its key can set. */
function keyFields(term: Term): SearchField[] {
  const field = termField(term)
  const { key } = term
  if (field !== null || key === null) {
    return field === null ? [] : [field]
  }
  if (isField(key)) {
    return [FIELD[key]]
  }
  if (isDate(key)) {
    return [DATES[key]]
  }
  if (!isFlag(key)) {
    return []
  }
  const flags: Record<string, readonly [SearchField, boolean]> = FLAGS[key]
  return Object.values(flags).map(([flag]) => flag)
}

function interpret(list: Term[], strict: boolean): ParseResult {
  const parsed: Record<string, string | boolean> = {}
  const words: string[] = []
  for (const term of list) {
    const read = meaning(term)
    if ('code' in read) {
      if (strict) {
        return { ok: false, code: read.code, key: term.key ?? undefined }
      }
      continue
    }
    if (read.field === 'text') {
      if (read.value !== '') {
        words.push(String(read.value))
      }
      continue
    }
    const before = parsed[read.field]
    if (strict && before !== undefined && before !== read.value) {
      return { ok: false, code: 'conflict' }
    }
    parsed[read.field] = read.value
  }
  if (words.length > 0) {
    parsed['text'] = words.join(' ')
  }
  return { ok: true, parsed: parsed }
}

/**
 * `line` as criteria, as the backend's `parse_search` reads it. Terms are separated by spaces;
 * `key:value` with a key of the syntax sets that criterion, any other term is text, the words
 * joined by one space. Refused with the backend's codes, at the first term at fault.
 */
export function parseSearch(line: string): ParseResult {
  const read = terms(line)
  const result = interpret(read.terms, true)
  return result.ok && read.unterminated ? { ok: false, code: 'unterminated_quote' } : result
}

/** What `line` asks for read leniently, as a form shows it: a term at fault is skipped, the last of two wins. */
export function readLeniently(line: string): ParsedSearch {
  const result = interpret(terms(line).terms, false)
  return result.ok ? result.parsed : {}
}

/** `value` as the syntax writes it: quoted when it holds a space, a colon or nothing; a double quote, which it cannot hold, dropped. */
function quoted(text: string): string {
  const bare = text.replaceAll('"', '')
  return bare === '' || /[\s:]/.test(bare) ? `"${bare}"` : bare
}

const KEY_OF: Partial<Record<SearchField, string>> = {
  from: 'from',
  to: 'to',
  subject: 'subject',
  folder: 'in',
  account: 'account',
  since: 'after',
  before: 'before',
}

/** The term that sets `field` to `value`; `null` for no term (an empty value, a flag not set). */
export function termFor(field: SearchField, value: string | boolean | undefined): string | null {
  if (value === undefined || value === '' || value === false) {
    return field === 'unread' && value === false ? 'is:read' : null
  }
  if (field === 'text') {
    return String(value).split(/[ \t\n\r\f\v]+/).filter(Boolean).map(quoted).join(' ') || null
  }
  if (typeof value === 'boolean') {
    return field === 'has_attachment' ? 'has:attachment' : `is:${field}`
  }
  const key = KEY_OF[field]
  return key === undefined ? null : `${key}:${quoted(value)}`
}

/** A line that asks for `parsed`: its words first, then its terms. */
export function lineOf(parsed: ParsedSearch): string {
  const order: SearchField[] = ['text', 'from', 'to', 'subject', 'unread', 'flagged', 'to_sort', 'has_attachment', 'since', 'before', 'folder', 'account']
  return order
    .map((field) => termFor(field, parsed[field]))
    .filter((term) => term !== null)
    .join(' ')
}

/** `line` without the spans of `cut`, each with the spaces after it. */
function without(line: string, cut: Term[]): string {
  let out = ''
  let at = 0
  for (const term of [...cut].sort((a, b) => a.start - b.start)) {
    out += line.slice(at, term.start)
    at = term.end
    while (at < line.length && SPACE.has(line.charAt(at))) {
      at += 1
    }
  }
  return (out + line.slice(at)).trimEnd()
}

/** `line` without `term`. */
export function withoutTerm(line: string, term: Term): string {
  return without(line, [term])
}

/**
 * `line` with `field` set to `value` (unset for `null`, as `termFor` reads it): the first term
 * whose key names it (one at fault too) rewritten in place, the others dropped, else the term
 * added at the end. `null` when
 * a quote left open keeps the line from being read.
 */
export function withField(line: string, field: Exclude<SearchField, 'text'>, value: string | boolean | undefined): string | null {
  const read = terms(line)
  if (read.unterminated) {
    return null
  }
  const setting = read.terms.filter((term) => keyFields(term).includes(field))
  const next = termFor(field, value)
  const [first, ...rest] = setting
  if (first === undefined) {
    const kept = line.trimEnd()
    return next === null ? kept : kept === '' ? next : `${kept} ${next}`
  }
  const edited = next === null ? line : line.slice(0, first.start) + next + line.slice(first.end)
  const shift = next === null ? 0 : next.length - (first.end - first.start)
  const moved = rest.map((term) => ({ ...term, start: term.start + shift, end: term.end + shift }))
  return without(edited, next === null ? setting : moved)
}

export type AccountChoice = { ok: true; account: string } | { ok: false; code: 'unknown_account' | 'ambiguous_account' }

/** The account `account:` names, as the backend's `account_by_name` finds it: `*`, an id, else the one account whose name, else login, it is, ignoring case. */
export function namedAccount(accounts: Account[], written: string): AccountChoice {
  if (written === '*') {
    return { ok: true, account: '*' }
  }
  if (/^[0-9]+$/.test(written)) {
    return accounts.some((account) => String(account.id) === String(Number(written)))
      ? { ok: true, account: String(Number(written)) }
      : { ok: false, code: 'unknown_account' }
  }
  const wanted = written.toLowerCase()
  let found = accounts.filter((account) => account.name?.toLowerCase() === wanted)
  if (found.length === 0) {
    found = accounts.filter((account) => account.login.toLowerCase() === wanted)
  }
  const [only] = found
  if (found.length === 1 && only !== undefined) {
    return { ok: true, account: String(only.id) }
  }
  return { ok: false, code: found.length === 0 ? 'unknown_account' : 'ambiguous_account' }
}

/** How `account:` names an account: by its name, else its login, when that finds it alone; else by its id. */
export function accountTerm(accounts: Account[], id: string): string {
  if (id === '*') {
    return id
  }
  const account = accounts.find((each) => String(each.id) === id)
  for (const name of [account?.name, account?.login]) {
    if (name && !/^[0-9]+$/.test(name)) {
      const found = namedAccount(accounts, name)
      if (found.ok && found.account === id) {
        return name
      }
    }
  }
  return id
}
