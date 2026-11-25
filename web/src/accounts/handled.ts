import type { Account } from '@/api/accounts.ts'

/** What an account keeps the Handled mark as; the star where it chose nothing else. */
export type HandledKind = 'star' | 'keyword' | 'label'
export const HANDLED_KINDS: readonly HandledKind[] = ['star', 'keyword', 'label']

/** The name a keyword or a label starts with, until another is written. */
export const HANDLED_NAMES: Record<Exclude<HandledKind, 'star'>, string> = {
  keyword: '$MailroomHandled',
  label: 'Handled',
}

export interface HandledMark {
  kind: HandledKind
  /** The keyword's or the label's; empty for the star. */
  name: string
}

const STAR: HandledMark = { kind: 'star', name: '' }

/** A mark as the API writes it: `star`, `keyword:<name>` or `label:<name>`. */
export function readHandled(mark: string | undefined): HandledMark {
  const at = mark?.indexOf(':') ?? -1
  if (mark === undefined || at === -1) {
    return STAR
  }
  const kind = mark.slice(0, at)
  return kind === 'keyword' || kind === 'label' ? { kind, name: mark.slice(at + 1) } : STAR
}

export function writeHandled({ kind, name }: HandledMark): string {
  return kind === 'star' ? kind : `${kind}:${name}`
}

/** Whether the account's server is known not to keep the mark it has: a keyword, where it keeps none. */
export function cannotKeepHandled(account: Pick<Account, 'handled'> | undefined, keywords: boolean | null): boolean {
  return readHandled(account?.handled).kind === 'keyword' && keywords === false
}

/** Whether the folder at `path` is the label the account keeps Handled as, which no folder picker offers. */
export function isHandledLabel(account: Pick<Account, 'handled'> | undefined, path: string): boolean {
  const mark = readHandled(account?.handled)
  return mark.kind === 'label' && mark.name === path
}
