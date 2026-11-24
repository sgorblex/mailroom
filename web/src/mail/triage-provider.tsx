import { useCallback, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode, type RefObject } from 'react'
import { useQueries, useQueryClient, type QueryObserverResult } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import type { AccountId } from '@/api/accounts.ts'
import {
  archiveMessages,
  flagMessages,
  junkMessages,
  moveMessages,
  moveToAccount,
  trashMessages,
  type FlagChange,
  type MailFolder,
  type OrganizeGroup,
  type SourceFolders,
} from '@/api/mail.ts'
import { ApiRequestError } from '@/api/http.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { asksFirst, isConversation, listedFolderOf, mixedFlags, newSenderIn, refsOf, shownOnly } from './conversations.ts'
import { MAIL_NS } from './i18n.ts'
import {
  ALL_MESSAGES_KEY,
  ALL_THREADS_KEY,
  foldersKey,
  foldersQuery,
  forgetCachedReads,
  setCachedReadFlags,
} from './queries.ts'
import { useToast } from './toast-context.ts'
import { DoneMessage } from './toolbar.tsx'
import {
  failureKey,
  folderState,
  isSameFolder,
  roleFolder,
  TriageContext,
  type Action,
  type Destination,
  type Failure,
  type Flags,
  type FolderState,
  type Matching,
  type MatchingAction,
  type NewSender,
  type Outcome,
  type SpamOffer,
  type Target,
  type Triage,
  type Undo,
} from './triage-context.ts'

/** The most refs one request takes; a larger selection goes in several. */
const BATCH = 50

/**
 * What belongs to one source (a folder or a search): a new source starts without it. `overrides`
 * are the flags this screen changed since the source's list was read, so a fresh read of a new
 * source is never masked by them.
 */
interface Scope {
  key: string
  selection: ReadonlyMap<string, Target>
  overrides: ReadonlyMap<string, Flags>
  outcome: Outcome | null
  picker: boolean
  accountPicker: boolean
  accountPickerInto: Destination | null
  matching: Matching | null
  bulk: MatchingAction | null
  newSender: NewSender | null
}

const NO_SELECTION: ReadonlyMap<string, Target> = new Map()
const NO_OVERRIDES: ReadonlyMap<string, Flags> = new Map()
const NO_ACCOUNTS: readonly AccountId[] = []

function freshScope(key: string): Scope {
  return {
    key,
    selection: NO_SELECTION,
    overrides: NO_OVERRIDES,
    outcome: null,
    picker: false,
    accountPicker: false,
    accountPickerInto: null,
    matching: null,
    bulk: null,
    newSender: null,
  }
}

/** Where a move-like action puts an account's messages; `undefined` while that is not known. */
function destination(action: Action, folders: MailFolder[] | undefined): string | undefined {
  switch (action.kind) {
    case 'move':
      return action.folder.path
    case 'moveToAccount':
      // In another account: never where a message already is.
      return undefined
    case 'archive':
      return roleFolder(folders, 'archive')?.path
    case 'junk':
      return roleFolder(folders, 'junk')?.path
    case 'trash':
      return roleFolder(folders, 'trash')?.path
    case 'flag':
      return undefined
  }
}

/**
 * `action` on the messages `targets` name, listed from `sourceFolder` (the folder shown;
 * `undefined` over every folder), each under its own `listed_folder` where it names one (a
 * view's, a search's).
 */
function request(
  action: Action,
  targets: Pick<Target, 'ref' | 'listed_folder'>[],
  signal: AbortSignal,
  sourceFolder: string | undefined,
): Promise<OrganizeGroup[]> {
  const refs = targets.map((target) => target.ref)
  const source: SourceFolders = {
    folder: sourceFolder,
    each: Object.fromEntries(targets.flatMap((target) => (target.listed_folder == null ? [] : [[target.ref, target.listed_folder]]))),
  }
  switch (action.kind) {
    case 'move':
      return moveMessages(refs, action.folder.path, signal, source)
    case 'moveToAccount':
      return moveToAccount(refs, action.account, action.folder.path, action.removal, signal)
    case 'archive':
      return archiveMessages(refs, signal, source)
    case 'junk':
      return junkMessages(refs, signal, source)
    case 'trash':
      return trashMessages(refs, signal, source)
    case 'flag':
      return flagMessages(refs, action.change, signal)
  }
}

/** The one sender of messages all of one account, to offer a filter on; `null` when they have several or none known. */
function oneSender(targets: Target[]): SpamOffer | null {
  const [first] = targets
  const address = first?.from?.address.trim() ?? ''
  const same = (target: Target): boolean =>
    target.account_id === first?.account_id && target.from?.address.trim().toLowerCase() === address.toLowerCase()
  return first === undefined || address === '' || !targets.every(same) ? null : { account: first.account_id, address }
}

/** `items` in requests of at most `BATCH`. */
function batches<T>(items: T[]): T[][] {
  const out: T[][] = []
  for (let start = 0; start < items.length; start += BATCH) {
    out.push(items.slice(start, start + BATCH))
  }
  return out
}

/** The flags `change` set that `before` did not have, as they were: what sets them back. */
function restoring(change: FlagChange, before: Flags): FlagChange {
  const back: FlagChange = {}
  for (const flag of ['seen', 'flagged', 'to_sort'] as const) {
    const value = change[flag]
    if (value !== undefined && before[flag] !== value) {
      back[flag] = before[flag]
    }
  }
  return back
}

/** The groups a refused batch would have had, each failed with the refusal's code. */
function refusedGroups(targets: Target[], error: unknown): Failure[] {
  const code = (error instanceof ApiRequestError ? error.code : undefined) ?? 'server_error'
  const groups = new Map<string, Failure>()
  for (const target of targets) {
    groups.set(JSON.stringify([target.account_id, target.folder]), {
      account: target.account_id,
      folder: target.folder,
      code,
    })
  }
  return [...groups.values()]
}

/** Each failing folder once per code: several batches can fail the same folder. */
function uniqueFailures(failures: Failure[]): Failure[] {
  const seen = new Set<string>()
  return failures.filter((failure) => {
    const key = failureKey(failure)
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
}

/** A value kept in state for rendering and in a ref for the async code that reads it after an await. */
function useMirrored<T>(initial: T): [T, RefObject<T>, (update: (value: T) => T) => void] {
  const [value, setValue] = useState(initial)
  const ref = useRef(initial)
  const set = useCallback((update: (value: T) => T) => {
    ref.current = update(ref.current)
    setValue(ref.current)
  }, [])
  return [value, ref, set]
}

/**
 * The mail screen's shared triage state: folder lists with their counts, the selection, the
 * open message, and what actions changed (rows moved away, flags), so the list, the sidebar and
 * the toolbar update in place. `sourceKey` names the list shown; a new one clears the selection.
 * `sourceFolder` is the folder it shows (`undefined` over every folder), which actions name.
 */
export function TriageProvider({
  sourceKey,
  sourceFolder,
  accounts = NO_ACCOUNTS,
  children,
}: {
  sourceKey: string
  sourceFolder?: string
  accounts?: readonly AccountId[]
  children: ReactNode
}): ReactElement {
  const navigate = useNavigate()
  const { t } = useTranslation(MAIL_NS)
  const toast = useToast()
  const unmountSignal = useUnmountSignal()
  const queryClient = useQueryClient()
  // The accounts whose folders the screen asked for: their queries are kept and shared from here.
  const [wanted, setWanted] = useState<readonly AccountId[]>([])
  const combineFolders = useCallback(
    (results: QueryObserverResult<MailFolder[]>[]): Record<string, FolderState> =>
      Object.fromEntries(results.map((result, index) => [String(wanted[index]), folderState(result)])),
    [wanted],
  )
  const folders = useQueries({ queries: wanted.map((account) => foldersQuery(account)), combine: combineFolders })
  const [removed, , setRemoved] = useMirrored<ReadonlySet<string>>(new Set())
  const [open, openRef, setOpenState] = useMirrored<Target | null>(null)
  const [scoped, scopedRef, setScoped] = useMirrored<Scope>(freshScope(sourceKey))
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const setBusyState = useCallback((value: boolean) => {
    busyRef.current = value
    setBusy(value)
  }, [])
  // What the lists show was changed behind the screen's back: each reads itself again.
  const reloadLists = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ALL_MESSAGES_KEY })
  }, [queryClient])
  const [spamOffer, setSpamOffer] = useState<SpamOffer | null>(null)
  const dismissSpamOffer = useCallback(() => {
    setSpamOffer(null)
  }, [])
  // The silent mark-read of each ref still in flight: the user's own action on that ref waits for
  // it, so the server sees (and the screen applies) the two in that order.
  const pendingSilent = useRef(new Map<string, Promise<void>>())
  // The source shown: a scope of another one is stale, and coming back to a source starts it anew.
  const currentKey = useRef(sourceKey)
  useLayoutEffect(() => {
    currentKey.current = sourceKey
    setScoped((previous) => (previous.key === sourceKey ? previous : freshScope(sourceKey)))
  }, [sourceKey, setScoped])
  const visible = useRef<Target[]>([])
  const visibleListeners = useRef(new Set<(rows: Target[]) => void>())
  const setVisible = useCallback((rows: Target[]) => {
    visible.current = rows
    for (const listener of visibleListeners.current) {
      listener(rows)
    }
  }, [])
  const visibleRows = useCallback(() => visible.current, [])
  const subscribeVisible = useCallback((listener: (rows: Target[]) => void) => {
    visibleListeners.current.add(listener)
    return () => {
      visibleListeners.current.delete(listener)
    }
  }, [])

  const scope = scoped.key === sourceKey ? scoped : freshScope(sourceKey)

  /** Changes the scope of `key`; once the source has changed, the change is dropped. */
  const updateScope = useCallback(
    (key: string, update: (scope: Scope) => Partial<Scope>) => {
      setScoped((previous) => {
        if (key !== currentKey.current) {
          return previous
        }
        const base = previous.key === key ? previous : freshScope(key)
        return { ...base, ...update(base) }
      })
    },
    [setScoped],
  )

  const loadFolders = useCallback(
    (account: AccountId, fresh: boolean) => {
      setWanted((value) => (value.includes(account) ? value : [...value, account]))
      if (fresh) {
        // The request on its way is dropped for the new one.
        void queryClient.invalidateQueries({ queryKey: foldersKey(account), refetchType: 'all' })
      }
    },
    [queryClient],
  )
  /** An account's folders as last read, for the async code that reads them after an await. */
  const loadedFoldersOf = useCallback(
    (account: AccountId): MailFolder[] | undefined =>
      queryClient.getQueryState(foldersKey(account))?.status === 'success'
        ? queryClient.getQueryData<MailFolder[]>(foldersKey(account))
        : undefined,
    [queryClient],
  )

  const adjustFolder = useCallback(
    (account: AccountId, path: string, unread: number, total: number) => {
      const list = loadedFoldersOf(account)
      if (list === undefined || (unread === 0 && total === 0)) {
        return
      }
      queryClient.setQueryData<MailFolder[]>(
        foldersKey(account),
        list.map((folder) =>
          isSameFolder(folder.path, path)
            ? { ...folder, unread: Math.max(0, folder.unread + unread), total: Math.max(0, folder.total + total) }
            : folder,
        ),
      )
    },
    [queryClient, loadedFoldersOf],
  )

  const overrides = scope.overrides
  const flagsOf = useCallback((target: Target): Flags => overrides.get(target.ref) ?? target.flags, [overrides])
  /** The flags changed in `key`'s scope, as the async code reads them after an await. */
  const overridesOf = useCallback(
    (key: string): ReadonlyMap<string, Flags> => {
      const current = scopedRef.current
      return current.key === key ? current.overrides : NO_OVERRIDES
    },
    [scopedRef],
  )

  /**
   * Takes back an action with its own requests (audited as any): moves the messages back from
   * where they landed, then reads the list again, or sets back the flags it changed.
   */
  const undoAction = useCallback(
    async (plan: Undo) => {
      if (busyRef.current) {
        return
      }
      setBusyState(true)
      setSpamOffer(null)
      const signal = unmountSignal()
      let failed = false
      // The flags set back, per ref.
      const restored = new Map<string, FlagChange>()
      try {
        const requests =
          plan.kind === 'move'
            ? plan.groups.flatMap((group) =>
                batches(group.refs).map((refs) => ({ change: {}, send: () => moveMessages(refs, group.folder, signal) })),
              )
            : plan.restores.flatMap((restore) =>
                batches(restore.refs).map((refs) => ({
                  change: restore.change,
                  send: () => flagMessages(refs, restore.change, signal),
                })),
              )
        for (const { change, send } of requests) {
          try {
            for (const group of await send()) {
              if (group.error !== null || !group.ok) {
                failed = true
              } else {
                for (const state of group.flags) {
                  restored.set(state.ref, change)
                }
              }
            }
          } catch {
            if (signal.aborted) {
              return
            }
            failed = true
          }
        }
      } finally {
        setBusyState(false)
      }
      const accounts = plan.kind === 'move' ? plan.groups.map((group) => group.account) : plan.accounts
      for (const account of new Set(accounts)) {
        loadFolders(account, true)
      }
      if (plan.kind === 'move') {
        reloadLists()
      } else {
        setCachedReadFlags(queryClient, (view) => restored.get(view.ref))
        updateScope(currentKey.current, (current) => {
          const overrides = new Map(current.overrides)
          // Only the flags set back: another changed since stays as it is now.
          for (const [ref, flags] of plan.before) {
            const change = restored.get(ref)
            if (change !== undefined) {
              overrides.set(ref, { ...(current.overrides.get(ref) ?? flags), ...change })
            }
          }
          return { overrides }
        })
      }
      toast.show(failed ? { message: t('undo.failed'), tone: 'error' } : { message: t('undo.done') })
    },
    [setBusyState, unmountSignal, loadFolders, reloadLists, updateScope, toast, t, queryClient],
  )

  const folderMarkedRead = useCallback(
    (account: AccountId, folders: readonly string[]) => {
      const inFolder = new Set(
        visible.current
          .filter((row) => row.account_id === account && folders.some((folder) => isSameFolder(row.folder, folder)))
          .map((row) => row.ref),
      )
      updateScope(currentKey.current, (current) => ({
        overrides: new Map([...current.overrides].filter(([ref, flags]) => flags.seen || !inFolder.has(ref))),
      }))
      setCachedReadFlags(queryClient, (view) =>
        view.account_id === account && folders.some((folder) => isSameFolder(view.folder, folder)) ? { seen: true } : undefined,
      )
      reloadLists()
    },
    [updateScope, reloadLists, queryClient],
  )

  const refreshed = useCallback(
    (key: string, rows: readonly Target[]) => {
      // The flags as the server read them now: a message opened again shows them. A conversation's
      // row says its messages' together, not its latest's.
      const read = new Map(rows.filter((row) => !isConversation(row)).map((row) => [row.ref, row.flags]))
      setCachedReadFlags(queryClient, (view) => {
        const flags = read.get(view.ref)
        return flags === undefined ? undefined : { seen: flags.seen, flagged: flags.flagged, to_sort: flags.to_sort }
      })
      const current = scopedRef.current
      const shown = openRef.current?.ref
      const fresh = rows.filter((row) => row.ref === shown || (current.key === key && current.overrides.has(row.ref)))
      if (fresh.length === 0) {
        return
      }
      updateScope(key, (scope) => {
        const overrides = new Map(scope.overrides)
        for (const { ref, flags } of fresh) {
          overrides.set(ref, { seen: flags.seen, flagged: flags.flagged, to_sort: flags.to_sort })
        }
        return { overrides }
      })
    },
    [scopedRef, openRef, updateScope, queryClient],
  )

  const act = useCallback(
    async (action: Action, targets: Target[], options: { silent?: boolean } = {}) => {
      const silent = options.silent === true
      if (!silent && busyRef.current) {
        return
      }
      const key = sourceKey
      // Another report of spam takes the last one's offer away, whatever comes of it.
      if (action.kind === 'junk' && !silent) {
        setSpamOffer(null)
      }
      // Messages already where the action would put them are left alone.
      const pending = targets.filter((target) => {
        const where = destination(action, loadedFoldersOf(target.account_id))
        return where === undefined || !isSameFolder(target.folder, where)
      })
      if (pending.length === 0) {
        return
      }
      let settle = (): void => undefined
      const settled = new Promise<void>((resolve) => {
        settle = resolve
      })
      // What this run stored per ref: a second mark-read of a ref still marking waits behind the
      // first instead of replacing it, so the reader's next action waits for both.
      const stored = new Map<string, Promise<void>>()
      if (silent) {
        for (const target of pending) {
          const previous = pendingSilent.current.get(target.ref)
          const entry = previous === undefined ? settled : Promise.all([previous, settled]).then(() => undefined)
          pendingSilent.current.set(target.ref, entry)
          stored.set(target.ref, entry)
        }
      } else {
        setBusyState(true)
        // The last outcome goes, so the live region empties and an identical one is read out again;
        // an action on rows ends a selection of every matching message.
        updateScope(key, () => ({ picker: false, accountPicker: false, outcome: null, matching: null }))
        toast.clear()
      }
      const signal = unmountSignal()
      let count = 0
      const failures: Failure[] = []
      // The first refused batch's error, and how many were refused: when every batch was, the
      // outcome is worded as that refusal.
      let refusal: unknown = null
      let refusedBatches = 0
      // Each message to act on with its row: a conversation's row takes every message of it.
      const items = pending.flatMap((target) => refsOf(target).map((ref) => ({ ref, target })))
      const batchCount = Math.ceil(items.length / BATCH)
      // The conversations a group of which failed: what became of each of their messages is not known.
      const unsure = new Set<Target>()
      // How to take it back: `null` once a group's messages cannot be found where they went.
      let undo: Undo | null =
        action.kind === 'flag'
          ? { kind: 'flag', accounts: [], restores: [], before: [] }
          : { kind: 'move', groups: [] }
      try {
        if (!silent) {
          const marking = pending.flatMap((target) => pendingSilent.current.get(target.ref) ?? [])
          await Promise.all(marking)
          if (signal.aborted) {
            return
          }
        }
        for (const batch of batches(items)) {
          const rows = [...new Set(batch.map((item) => item.target))]
          let groups: OrganizeGroup[]
          try {
            groups = await request(
              action,
              batch.map((item) => ({ ref: item.ref, listed_folder: listedFolderOf(item.target, item.ref) })),
              signal,
              sourceFolder,
            )
          } catch (error) {
            if (signal.aborted) {
              return
            }
            refusedBatches += 1
            refusal ??= error
            failures.push(...refusedGroups(rows, error))
            for (const row of rows.filter(isConversation)) {
              unsure.add(row)
            }
            continue
          }
          for (const group of groups) {
            const conversations = rows.filter((row) => isConversation(row) && row.account_id === group.account)
            if (group.error !== null || !group.ok) {
              // A conversation's messages already where the action puts them are left alone, as rows are above.
              const where = destination(action, loadedFoldersOf(group.account))
              if (conversations.length > 0 && where !== undefined && isSameFolder(group.folder, where)) {
                continue
              }
              for (const row of conversations) {
                unsure.add(row)
              }
              const inBoth = action.kind === 'moveToAccount' ? (group.new_refs?.length ?? 0) - group.count : 0
              failures.push({
                account: group.account,
                folder: group.folder,
                code: group.error?.code ?? 'server_error',
                ...(inBoth > 0 ? { inBoth } : {}),
              })
              // A move to another account may have moved some before it failed: they count, but
              // which rows they were is not known, so the list shows them until it is read again.
              count += group.count
              continue
            }
            count += group.count
            const members = rows.filter(
              (target) => !isConversation(target) && target.account_id === group.account && target.folder === group.folder,
            )
            if (action.kind === 'flag') {
              applyFlags(group, members, action.change)
            } else {
              applyMove(group, members, action)
              undo = undoMove(undo, group)
            }
          }
        }
        const whole = pending.filter((target) => isConversation(target) && !unsure.has(target))
        if (whole.length > 0) {
          applyConversations(whole, action)
        }
        if (count > 0) {
          // An open conversation's messages show their flags and folders as read before.
          void queryClient.invalidateQueries({ queryKey: ALL_THREADS_KEY })
        }
      } finally {
        if (silent) {
          for (const target of pending) {
            if (pendingSilent.current.get(target.ref) === stored.get(target.ref)) {
              pendingSilent.current.delete(target.ref)
            }
          }
          settle()
        } else {
          setBusyState(false)
        }
      }
      if (silent) {
        return
      }
      const outcome: Outcome =
        refusedBatches === batchCount
          ? { kind: 'refused', action, error: refusal }
          : { kind: 'done', action, count, failures: uniqueFailures(failures) }
      if (outcome.kind === 'refused' || outcome.failures.length > 0 || count === 0) {
        updateScope(key, () => ({ outcome }))
        return
      }
      if (action.kind === 'junk') {
        setSpamOffer(oneSender(pending))
      }
      // Done in full: the toast says so, with Undo where it can be taken back.
      const plan = undo
      const message = <DoneMessage action={action} count={count} />
      if (action.kind === 'moveToAccount') {
        toast.show({ message })
      } else if (plan?.kind === 'flag' && plan.restores.length === 0) {
        // Every message already was as the action left it.
        toast.show({ message })
      } else if (plan === null) {
        toast.show({
          message: (
            <>
              {message} {t('undo.impossible')}
            </>
          ),
        })
      } else {
        toast.show({
          message,
          action: {
            label: t('undo.undo'),
            run: () => {
              void undoAction(plan)
            },
          },
        })
      }

      /** `plan` with the group's messages to move back from where they landed, or `null` when the server did not say. */
      function undoMove(plan: Undo | null, group: OrganizeGroup): Undo | null {
        if (plan?.kind !== 'move' || group.new_refs === null || group.new_refs.length < group.count) {
          return null
        }
        const same = plan.groups.find((each) => each.account === group.account && each.folder === group.folder)
        if (same !== undefined) {
          same.refs.push(...group.new_refs)
          return plan
        }
        plan.groups.push({ account: group.account, folder: group.folder, refs: [...group.new_refs] })
        return plan
      }

      function applyFlags(group: OrganizeGroup, members: Target[], change: FlagChange): void {
        let unread = 0
        const changed = overridesOf(key)
        const next = new Map(changed)
        for (const target of members) {
          const before = changed.get(target.ref) ?? target.flags
          const read = group.flags.find((state) => state.ref === target.ref)
          // A flag that could not be read back: the change stands.
          const after = {
            seen: read?.seen ?? change.seen ?? before.seen,
            flagged: read?.flagged ?? change.flagged ?? before.flagged,
            to_sort: read?.to_sort ?? change.to_sort ?? before.to_sort,
          }
          next.set(target.ref, after)
          setCachedReadFlags(queryClient, (view) => (view.ref === target.ref ? after : undefined))
          unread += Number(!after.seen) - Number(!before.seen)
          if (undo?.kind === 'flag') {
            undo.before.push([target.ref, before])
            const back = restoring(change, before)
            if (Object.keys(back).length > 0) {
              const restore = undo.restores.find((each) => JSON.stringify(each.change) === JSON.stringify(back))
              if (restore === undefined) {
                undo.restores.push({ change: back, refs: [target.ref] })
              } else {
                restore.refs.push(target.ref)
              }
            }
          }
        }
        if (undo?.kind === 'flag' && !undo.accounts.includes(group.account)) {
          undo.accounts.push(group.account)
        }
        updateScope(key, () => ({ overrides: next }))
        adjustFolder(group.account, group.folder, unread, 0)
      }

      function applyMove(group: OrganizeGroup, members: Target[], move: Action): void {
        const refs = new Set(members.map((target) => target.ref))
        forgetCachedReads(queryClient, refs)
        const changed = overridesOf(key)
        const unread = members.filter((target) => !(changed.get(target.ref) ?? target.flags).seen).length
        setRemoved((value) => new Set([...value, ...refs]))
        updateScope(key, (current) => ({
          selection: new Map([...current.selection].filter(([ref]) => !refs.has(ref))),
        }))
        adjustFolder(group.account, group.folder, -unread, -members.length)
        if (move.kind === 'moveToAccount') {
          adjustFolder(move.account, move.folder.path, unread, members.length)
          const trash = roleFolder(loadedFoldersOf(group.account), 'trash')?.path
          if (move.removal === 'trash' && trash !== undefined && !isSameFolder(trash, group.folder)) {
            adjustFolder(group.account, trash, unread, members.length)
          }
        } else {
          const where = destination(move, loadedFoldersOf(group.account))
          if (where !== undefined) {
            adjustFolder(group.account, where, unread, members.length)
          }
        }
        closeOpen(refs)
      }

      /** The open message is gone from where it was read: the pane closes. */
      function closeOpen(refs: ReadonlySet<string>): void {
        const current = openRef.current
        if (current !== null && refs.has(current.ref)) {
          setOpenState(() => null)
          void navigate({ to: '/mail', search: (previous) => ({ ...previous, message: undefined, thread: undefined }) })
        }
      }

      /**
       * Conversations every message of which the action reached: their rows change as one, and
       * their accounts' folder counts are read again, as which folders their messages were in is
       * not known here. A flag change is taken back message by message only where the row said
       * each one's flag.
       */
      function applyConversations(rows: Target[], done: Action): void {
        const accounts = new Set(rows.map((row) => row.account_id))
        if (done.kind === 'moveToAccount') {
          accounts.add(done.account)
        }
        for (const account of accounts) {
          loadFolders(account, true)
        }
        if (done.kind !== 'flag') {
          const refs = new Set(rows.flatMap((row) => [row.ref, ...refsOf(row)]))
          forgetCachedReads(queryClient, refs)
          setRemoved((value) => new Set([...value, ...refs]))
          updateScope(key, (current) => ({
            selection: new Map([...current.selection].filter(([ref]) => !refs.has(ref))),
          }))
          closeOpen(refs)
          return
        }
        const { change } = done
        const members = new Set(rows.flatMap((row) => [row.ref, ...refsOf(row)]))
        setCachedReadFlags(queryClient, (view) => (members.has(view.ref) ? change : undefined))
        const changed = overridesOf(key)
        const next = new Map(changed)
        for (const row of rows) {
          const before = changed.get(row.ref) ?? row.flags
          next.set(row.ref, {
            seen: change.seen ?? before.seen,
            flagged: change.flagged ?? before.flagged,
            to_sort: change.to_sort ?? before.to_sort,
          })
          const mixed = mixedFlags(row)
          if (mixed.some((flag) => change[flag] !== undefined)) {
            undo = null
          } else if (undo?.kind === 'flag') {
            undo.before.push([row.ref, before])
            const back = restoring(change, before)
            if (Object.keys(back).length > 0) {
              undo.restores.push({ change: back, refs: [...refsOf(row)] })
            }
            if (!undo.accounts.includes(row.account_id)) {
              undo.accounts.push(row.account_id)
            }
          }
        }
        updateScope(key, () => ({ overrides: next }))
      }
    },
    [
      sourceKey,
      sourceFolder,
      setBusyState,
      undoAction,
      toast,
      t,
      loadedFoldersOf,
      overridesOf,
      openRef,
      unmountSignal,
      updateScope,
      setRemoved,
      setOpenState,
      adjustFolder,
      loadFolders,
      navigate,
      queryClient,
    ],
  )

  const select = useCallback(
    (targets: Target[], selected: boolean) => {
      updateScope(sourceKey, (current) => {
        const selection = new Map(current.selection)
        for (const target of targets) {
          if (selected) {
            selection.set(target.ref, target)
          } else {
            selection.delete(target.ref)
          }
        }
        return { selection, matching: null }
      })
    },
    [sourceKey, updateScope],
  )

  const clearSelection = useCallback(() => {
    updateScope(sourceKey, () => ({ selection: NO_SELECTION, matching: null }))
  }, [sourceKey, updateScope])
  const selectMatching = useCallback(
    (matching: Matching) => {
      updateScope(sourceKey, () => ({ matching }))
    },
    [sourceKey, updateScope],
  )
  const setBulk = useCallback(
    (bulk: MatchingAction | null) => {
      updateScope(sourceKey, () => ({ bulk, picker: false, accountPicker: false }))
    },
    [sourceKey, updateScope],
  )
  const bulkDone = useCallback(
    (touched: AccountId[]) => {
      for (const account of new Set(touched)) {
        loadFolders(account, true)
      }
      updateScope(sourceKey, () => ({ bulk: null, matching: null, selection: NO_SELECTION, overrides: NO_OVERRIDES }))
      reloadLists()
    },
    [sourceKey, updateScope, loadFolders, reloadLists],
  )
  const setOpen = useCallback(
    (target: Target | null) => {
      setOpenState(() => target)
    },
    [setOpenState],
  )
  const dismissOutcome = useCallback(() => {
    updateScope(sourceKey, () => ({ outcome: null }))
  }, [sourceKey, updateScope])
  // One picker open at a time.
  const setPicker = useCallback(
    (picker: boolean) => {
      updateScope(sourceKey, (current) => ({ picker, accountPicker: picker ? false : current.accountPicker }))
    },
    [sourceKey, updateScope],
  )
  const setAccountPicker = useCallback(
    (accountPicker: boolean, into?: Destination) => {
      updateScope(sourceKey, (current) => ({
        accountPicker,
        accountPickerInto: into ?? null,
        picker: accountPicker ? false : current.picker,
      }))
    },
    [sourceKey, updateScope],
  )

  // A conversation of several whose newest sender is new to it is not moved whole unasked.
  const run = useCallback(
    async (action: Action, targets: Target[], options: { silent?: boolean } = {}) => {
      if (options.silent !== true && !busyRef.current && asksFirst(action, targets)) {
        updateScope(sourceKey, () => ({ newSender: { action, targets } }))
        return
      }
      await act(action, targets, options)
    },
    [act, sourceKey, updateScope],
  )
  const answerNewSender = useCallback(
    (choice: 'whole' | 'shown' | null) => {
      const asked = scope.newSender
      updateScope(sourceKey, () => ({ newSender: null }))
      if (asked === null || choice === null) {
        return
      }
      void act(asked.action, choice === 'whole' ? asked.targets : asked.targets.map((target) => (newSenderIn(target) ? shownOnly(target) : target)))
    },
    [act, scope.newSender, sourceKey, updateScope],
  )

  const value: Triage = {
    accounts,
    folders,
    loadFolders,
    flagsOf,
    removed,
    selection: scope.selection,
    select,
    clearSelection,
    matching: scope.matching,
    selectMatching,
    bulk: scope.bulk,
    setBulk,
    bulkDone,
    open,
    setOpen,
    setVisible,
    visibleRows,
    subscribeVisible,
    targets: scope.selection.size > 0 ? [...scope.selection.values()] : open === null ? [] : [open],
    busy,
    outcome: scope.outcome,
    dismissOutcome,
    folderMarkedRead,
    refreshed,
    spamOffer,
    dismissSpamOffer,
    run,
    newSender: scope.newSender,
    answerNewSender,
    picker: scope.picker,
    setPicker,
    accountPicker: scope.accountPicker,
    accountPickerInto: scope.accountPickerInto,
    setAccountPicker,
  }
  return <TriageContext value={value}>{children}</TriageContext>
}
