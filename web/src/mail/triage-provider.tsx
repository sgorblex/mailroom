import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode, type RefObject } from 'react'
import { useNavigate } from '@tanstack/react-router'

import {
  archiveMessages,
  flagMessages,
  listMailFolders,
  moveMessages,
  trashMessages,
  type FlagChange,
  type MailFolder,
  type OrganizeGroup,
} from '@/api/mail.ts'
import { ApiRequestError } from '@/api/http.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import {
  failureKey,
  isSameFolder,
  loadedFolders,
  roleFolder,
  TriageContext,
  type Action,
  type Failure,
  type Flags,
  type FolderState,
  type Outcome,
  type Target,
  type Triage,
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
}

const NO_SELECTION: ReadonlyMap<string, Target> = new Map()
const NO_OVERRIDES: ReadonlyMap<string, Flags> = new Map()

function freshScope(key: string): Scope {
  return { key, selection: NO_SELECTION, overrides: NO_OVERRIDES, outcome: null, picker: false }
}

/** Where a move-like action puts an account's messages; `undefined` while that is not known. */
function destination(action: Action, folders: MailFolder[] | undefined): string | undefined {
  switch (action.kind) {
    case 'move':
      return action.folder.path
    case 'archive':
      return roleFolder(folders, 'archive')?.path
    case 'trash':
      return roleFolder(folders, 'trash')?.path
    case 'flag':
      return undefined
  }
}

function request(action: Action, refs: string[], signal: AbortSignal): Promise<OrganizeGroup[]> {
  switch (action.kind) {
    case 'move':
      return moveMessages(refs, action.folder.path, signal)
    case 'archive':
      return archiveMessages(refs, signal)
    case 'trash':
      return trashMessages(refs, signal)
    case 'flag':
      return flagMessages(refs, action.change, signal)
  }
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
 */
export function TriageProvider({ sourceKey, children }: { sourceKey: string; children: ReactNode }): ReactElement {
  const navigate = useNavigate()
  const unmountSignal = useUnmountSignal()
  const [folders, foldersRef, setFolders] = useMirrored<Record<string, FolderState>>({})
  const [removed, , setRemoved] = useMirrored<ReadonlySet<string>>(new Set())
  const [open, openRef, setOpenState] = useMirrored<Target | null>(null)
  const [scoped, scopedRef, setScoped] = useMirrored<Scope>(freshScope(sourceKey))
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
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
  const setVisible = useCallback((rows: Target[]) => {
    visible.current = rows
  }, [])
  const visibleRows = useCallback(() => visible.current, [])
  const folderRequests = useRef(new Map<string, AbortController>())

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

  useEffect(() => {
    const requests = folderRequests.current
    return () => {
      for (const controller of requests.values()) {
        controller.abort()
      }
    }
  }, [])

  const loadFolders = useCallback(
    (account: string, fresh: boolean) => {
      if (!fresh && foldersRef.current[account] !== undefined) {
        return
      }
      folderRequests.current.get(account)?.abort()
      const controller = new AbortController()
      folderRequests.current.set(account, controller)
      // A reload keeps showing the folders until it answers.
      setFolders((value) => (value[account]?.kind === 'loaded' ? value : { ...value, [account]: { kind: 'loading' } }))
      listMailFolders(account, controller.signal).then(
        (list) => {
          setFolders((value) => ({ ...value, [account]: { kind: 'loaded', value: list } }))
        },
        (error: unknown) => {
          if (!controller.signal.aborted) {
            setFolders((value) => ({ ...value, [account]: { kind: 'failed', error } }))
          }
        },
      )
    },
    [foldersRef, setFolders],
  )

  const adjustFolder = useCallback(
    (account: string, path: string, unread: number, total: number) => {
      setFolders((value) => {
        const state = value[account]
        if (state?.kind !== 'loaded' || (unread === 0 && total === 0)) {
          return value
        }
        const list = state.value.map((folder) =>
          isSameFolder(folder.path, path)
            ? { ...folder, unread: Math.max(0, folder.unread + unread), total: Math.max(0, folder.total + total) }
            : folder,
        )
        return { ...value, [account]: { kind: 'loaded', value: list } }
      })
    },
    [setFolders],
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

  const run = useCallback(
    async (action: Action, targets: Target[], options: { silent?: boolean } = {}) => {
      const silent = options.silent === true
      if (!silent && busyRef.current) {
        return
      }
      const key = sourceKey
      // Messages already where the action would put them are left alone.
      const pending = targets.filter((target) => {
        const where = destination(action, loadedFolders(foldersRef.current[target.account_id]))
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
        busyRef.current = true
        setBusy(true)
        // The last outcome goes, so the live region empties and an identical one is read out again.
        updateScope(key, () => ({ picker: false, outcome: null }))
      }
      const signal = unmountSignal()
      let count = 0
      const failures: Failure[] = []
      // The first refused batch's error, and how many were refused: when every batch was, the
      // outcome is worded as that refusal.
      let refusal: unknown = null
      let refusedBatches = 0
      const batches = Math.ceil(pending.length / BATCH)
      try {
        if (!silent) {
          const marking = pending.flatMap((target) => pendingSilent.current.get(target.ref) ?? [])
          await Promise.all(marking)
          if (signal.aborted) {
            return
          }
        }
        for (let start = 0; start < pending.length; start += BATCH) {
          const batch = pending.slice(start, start + BATCH)
          let groups: OrganizeGroup[]
          try {
            groups = await request(action, batch.map((target) => target.ref), signal)
          } catch (error) {
            if (signal.aborted) {
              return
            }
            refusedBatches += 1
            refusal ??= error
            failures.push(...refusedGroups(batch, error))
            continue
          }
          for (const group of groups) {
            if (group.error !== null || !group.ok) {
              failures.push({ account: group.account, folder: group.folder, code: group.error?.code ?? 'server_error' })
              continue
            }
            count += group.count
            const members = batch.filter((target) => target.account_id === group.account && target.folder === group.folder)
            if (action.kind === 'flag') {
              applyFlags(group, members, action.change)
            } else {
              applyMove(group, members, action)
            }
          }
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
          busyRef.current = false
          setBusy(false)
        }
      }
      if (silent) {
        return
      }
      const outcome: Outcome =
        refusedBatches === batches
          ? { kind: 'refused', action, error: refusal }
          : { kind: 'done', action, count, failures: uniqueFailures(failures) }
      updateScope(key, () => ({ outcome }))

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
          }
          next.set(target.ref, after)
          unread += Number(!after.seen) - Number(!before.seen)
        }
        updateScope(key, () => ({ overrides: next }))
        adjustFolder(group.account, group.folder, unread, 0)
      }

      function applyMove(group: OrganizeGroup, members: Target[], move: Action): void {
        const refs = new Set(members.map((target) => target.ref))
        const changed = overridesOf(key)
        const unread = members.filter((target) => !(changed.get(target.ref) ?? target.flags).seen).length
        setRemoved((value) => new Set([...value, ...refs]))
        updateScope(key, (current) => ({
          selection: new Map([...current.selection].filter(([ref]) => !refs.has(ref))),
        }))
        adjustFolder(group.account, group.folder, -unread, -members.length)
        const where = destination(move, loadedFolders(foldersRef.current[group.account]))
        if (where !== undefined) {
          adjustFolder(group.account, where, unread, members.length)
        }
        const current = openRef.current
        if (current !== null && refs.has(current.ref)) {
          setOpenState(() => null)
          void navigate({ to: '/mail', search: (previous) => ({ ...previous, message: undefined }) })
        }
      }
    },
    [
      sourceKey,
      foldersRef,
      overridesOf,
      openRef,
      unmountSignal,
      updateScope,
      setRemoved,
      setOpenState,
      adjustFolder,
      navigate,
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
        return { selection }
      })
    },
    [sourceKey, updateScope],
  )

  const clearSelection = useCallback(() => {
    updateScope(sourceKey, () => ({ selection: NO_SELECTION }))
  }, [sourceKey, updateScope])
  const setOpen = useCallback(
    (target: Target | null) => {
      setOpenState(() => target)
    },
    [setOpenState],
  )
  const dismissOutcome = useCallback(() => {
    updateScope(sourceKey, () => ({ outcome: null }))
  }, [sourceKey, updateScope])
  const setPicker = useCallback(
    (picker: boolean) => {
      updateScope(sourceKey, () => ({ picker }))
    },
    [sourceKey, updateScope],
  )

  const value: Triage = {
    folders,
    loadFolders,
    flagsOf,
    removed,
    selection: scope.selection,
    select,
    clearSelection,
    open,
    setOpen,
    setVisible,
    visibleRows,
    targets: scope.selection.size > 0 ? [...scope.selection.values()] : open === null ? [] : [open],
    busy,
    outcome: scope.outcome,
    dismissOutcome,
    run,
    picker: scope.picker,
    setPicker,
  }
  return <TriageContext value={value}>{children}</TriageContext>
}
