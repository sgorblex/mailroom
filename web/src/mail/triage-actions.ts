import { useEffect } from 'react'

import type { MailFolder } from '@/api/mail.ts'

import { isSameFolder, loadedFolders, roleFolder, useTriage, type Target } from './triage-context.ts'

/** The actions the toolbar and the shortcuts offer for the current targets; `null` is not offered. */
export interface TriageActions {
  targets: Target[]
  archive: (() => void) | null
  trash: (() => void) | null
  /** Star, or unstar when every target is starred: `flagged` is what it sets. */
  star: { flagged: boolean; run: () => void } | null
  /** Mark read when a target is unread, else mark unread: `seen` is what it sets. */
  read: { seen: boolean; run: () => void } | null
  markUnread: (() => void) | null
  /** The folders to move to: the account's selectable ones, without Drafts, Sent and the folder they are in. */
  moveChoices: MailFolder[] | null
  move: (folder: MailFolder) => void
}

export function useTriageActions(): TriageActions {
  const triage = useTriage()
  const { targets, run, flagsOf, loadFolders } = triage
  const accounts = [...new Set(targets.map((target) => target.account_id))]
  const accountsKey = JSON.stringify(accounts)

  useEffect(() => {
    for (const account of JSON.parse(accountsKey) as string[]) {
      loadFolders(account, false)
    }
  }, [accountsKey, loadFolders])

  const foldersOf = (account: string): MailFolder[] | undefined => loadedFolders(triage.folders[account])
  const outside = (role: 'archive' | 'trash'): Target[] =>
    targets.filter((target) => {
      const where = roleFolder(foldersOf(target.account_id), role)?.path
      return where === undefined || !isSameFolder(target.folder, where)
    })

  if (targets.length === 0) {
    return {
      targets,
      archive: null,
      trash: null,
      star: null,
      read: null,
      markUnread: null,
      moveChoices: null,
      move: () => undefined,
    }
  }

  const canArchive =
    accounts.every((account) => roleFolder(foldersOf(account), 'archive') !== undefined) && outside('archive').length > 0
  const flagged = !targets.every((target) => flagsOf(target).flagged)
  const seen = targets.some((target) => !flagsOf(target).seen)
  const [account] = accounts
  const folders = accounts.length === 1 && account !== undefined ? foldersOf(account) : undefined
  const sources = [...new Set(targets.map((target) => target.folder))]
  const [source] = sources
  return {
    targets,
    archive: canArchive ? () => void run({ kind: 'archive' }, targets) : null,
    trash: outside('trash').length > 0 ? () => void run({ kind: 'trash' }, targets) : null,
    star: { flagged, run: () => void run({ kind: 'flag', change: { flagged } }, targets) },
    read: { seen, run: () => void run({ kind: 'flag', change: { seen } }, targets) },
    markUnread: () => void run({ kind: 'flag', change: { seen: false } }, targets),
    moveChoices:
      folders?.filter(
        (folder) =>
          folder.selectable &&
          folder.role !== 'drafts' &&
          folder.role !== 'sent' &&
          !(sources.length === 1 && source !== undefined && isSameFolder(folder.path, source)),
      ) ?? null,
    move: (folder) => void run({ kind: 'move', folder }, targets),
  }
}
