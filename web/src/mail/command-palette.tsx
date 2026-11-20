import { useContext, useEffect, useId, useRef, useState, type KeyboardEvent, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from '@tanstack/react-router'

import type { Account, AccountId } from '@/api/accounts.ts'
import { AuthContext } from '@/auth/auth-context.ts'
import { navPages } from '@/auth/nav-pages.ts'
import { cn } from '@/lib/utils.ts'
import { Dialog } from '@/components/ui/dialog.tsx'

import { MAIL_ACTIONS, runnable } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { useDraftFilter } from './filter-draft.ts'
import { useMuting } from './mute-context.ts'
import { MAIL_NS } from './i18n.ts'
import { folderName } from './messages.ts'
import { matchItems, storedRecents, storeRecent, type PaletteItem } from './palette.ts'
import { screenContext } from './shortcuts.ts'
import { useTriageActions } from './triage-actions.ts'
import { loadedFolders, useTriage } from './triage-context.ts'

const GROUPS = ['recent', 'actions', 'folders', 'accounts', 'pages'] as const

/**
 * The command palette (Ctrl/⌘+K): a combobox over the actions the open or selected messages can
 * take now, the folders and the accounts, and the app's pages. Its words match by the start of a
 * word, else anywhere; on no query it lists the recent choices first.
 */
export function CommandPalette({
  accounts,
  accountId,
  onClose,
}: {
  accounts: readonly Account[]
  accountId: AccountId
  onClose: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const items = usePaletteItems(accounts, accountId)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  // Read once: choosing an item, the only change to them, closes the palette.
  const [recents] = useState(storedRecents)
  const listId = useId()
  const list = useRef<HTMLUListElement>(null)

  const groups = paletteGroups(items, query, recents)
  const options = groups.flatMap((group) => group.items)
  const current = Math.min(active, options.length - 1)
  const optionId = (index: number): string => `${listId}-${String(index)}`

  useEffect(() => {
    // jsdom has no scrollIntoView.
    const option: Partial<Pick<Element, 'scrollIntoView'>> | null =
      list.current?.querySelector('[aria-selected="true"]') ?? null
    option?.scrollIntoView?.({ block: 'nearest' })
  }, [current])

  function choose(item: PaletteItem): void {
    storeRecent(item.id)
    onClose()
    item.run()
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (options.length === 0) {
      return
    }
    const last = options.length - 1
    const moves: Record<string, number> = {
      ArrowDown: current === last ? 0 : current + 1,
      ArrowUp: current <= 0 ? last : current - 1,
      PageDown: last,
      PageUp: 0,
    }
    const next = moves[event.key]
    if (next !== undefined) {
      event.preventDefault()
      setActive(next)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const item = options[current]
      if (item !== undefined) {
        choose(item)
      }
    }
  }

  const starts = groups.map((_, at) => groups.slice(0, at).reduce((sum, group) => sum + group.items.length, 0))
  return (
    <Dialog label={t('palette.title')} onCancel={onClose}>
      <div className="flex w-[min(28rem,calc(100vw-4rem))] flex-col gap-2">
        <input
          type="text"
          role="combobox"
          data-palette
          aria-label={t('palette.input')}
          aria-expanded={options.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={options.length > 0 ? optionId(current) : undefined}
          placeholder={t('palette.placeholder')}
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
        />
        <ul ref={list} id={listId} role="listbox" aria-label={t('palette.results')} className="max-h-80 overflow-y-auto">
          {groups.map((group, groupAt) => (
            <li key={group.name} role="presentation">
              <div id={`${listId}-${group.name}`} role="presentation" className="px-2 pt-2 text-xs font-medium text-muted-foreground">
                {t(`palette.${group.name}`)}
              </div>
              <ul role="group" aria-labelledby={`${listId}-${group.name}`}>
                {group.items.map((item, itemAt) => {
                  const at = (starts[groupAt] ?? 0) + itemAt
                  return (
                    // The combobox takes the keys: focus stays in it.
                    // eslint-disable-next-line jsx-a11y/click-events-have-key-events
                    <li
                      key={item.id}
                      id={optionId(at)}
                      role="option"
                      aria-selected={at === current}
                      onMouseMove={() => {
                        setActive(at)
                      }}
                      onClick={() => {
                        choose(item)
                      }}
                      className={cn(
                        'flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm',
                        at === current && 'bg-accent text-accent-foreground',
                      )}
                    >
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {item.detail !== undefined && (
                        <span className="truncate text-xs text-muted-foreground">{item.detail}</span>
                      )}
                      {item.shortcut !== undefined && (
                        <kbd className="rounded border bg-muted px-1.5 font-mono text-xs">{item.shortcut}</kbd>
                      )}
                    </li>
                  )
                })}
              </ul>
            </li>
          ))}
        </ul>
        {options.length === 0 && (
          <p role="status" className="px-2 text-sm text-muted-foreground">
            {t('palette.none')}
          </p>
        )}
      </div>
    </Dialog>
  )
}

/** The palette's groups for `query`, empty ones left out: the recent choices first when there is none. */
function paletteGroups(
  items: readonly PaletteItem[],
  query: string,
  recents: readonly string[],
): { name: (typeof GROUPS)[number]; items: PaletteItem[] }[] {
  const recentIds = query.trim() === '' ? recents : []
  const recent = recentIds.flatMap((id) => items.filter((item) => item.id === id))
  const rest = matchItems(
    items.filter((item) => !recentIds.includes(item.id)),
    query,
  )
  return GROUPS.map((name) => ({
    name,
    items: name === 'recent' ? recent : rest.filter((item) => item.group === name),
  })).filter((group) => group.items.length > 0)
}

/** Everything the palette offers now; every account's folders are loaded for it. */
function usePaletteItems(accounts: readonly Account[], accountId: AccountId): PaletteItem[] {
  const { t } = useTranslation(MAIL_NS)
  const triage = useTriage()
  const actions = useTriageActions()
  const compose = useCompose()
  const navigate = useNavigate()
  const auth = useContext(AuthContext)
  const { loadFolders } = triage

  useEffect(() => {
    for (const account of accounts) {
      loadFolders(account.id, false)
    }
  }, [accounts, loadFolders])

  const draftFilter = useDraftFilter()
  const muting = useMuting()
  const context = { ...screenContext(triage, actions, compose, accountId), draftFilter, muting }
  const state = { busy: triage.busy, composing: compose.session !== null }
  const actionItems = MAIL_ACTIONS.filter(
    (action) => runnable(action, context, state) && (action.listed?.(context) ?? true),
  ).map(
    (action): PaletteItem => ({
      id: `action:${action.id}`,
      group: 'actions',
      label: t(action.label(context)),
      shortcut: action.shortcut,
      run: () => {
        action.run(context)
      },
    }),
  )
  const folderItems = accounts.flatMap((account) =>
    (loadedFolders(triage.folders[account.id]) ?? [])
      .filter((folder) => folder.selectable)
      .map(
        (folder): PaletteItem => ({
          id: `folder:${String(account.id)}:${folder.path}`,
          group: 'folders',
          label: folderName(t, folder),
          detail: account.display_name,
          run: () => {
            void navigate({ to: '/mail', search: { account: account.id, folder: folder.path } })
          },
        }),
      ),
  )
  const accountItems = accounts.map(
    (account): PaletteItem => ({
      id: `account:${String(account.id)}`,
      group: 'accounts',
      label: account.display_name,
      run: () => {
        void navigate({ to: '/mail', search: { account: account.id } })
      },
    }),
  )
  const signedIn = auth?.status === 'signed-in' ? auth.user : null
  const pageItems = navPages(auth?.features ?? new Set(), signedIn?.is_admin === true).map(
    (page): PaletteItem => ({
      id: `page:${page.to}`,
      group: 'pages',
      label: t(page.label),
      run: () => {
        void navigate({ to: page.to })
      },
    }),
  )
  return [...actionItems, ...folderItems, ...accountItems, ...pageItems]
}
