import { useCallback, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link } from '@tanstack/react-router'
import {
  Archive,
  ChevronRight,
  FilePen,
  Folder,
  FolderClosed,
  Inbox,
  Mails,
  MoreHorizontal,
  Send,
  ShieldAlert,
  Star,
  Trash2,
  type LucideIcon,
} from 'lucide-react'

import type { FolderRole, MailFolder } from '@/api/mail.ts'
import { Menu, type MenuItem } from '@/components/ui/menu.tsx'
import { cn } from '@/lib/utils.ts'

import type { FolderDialog } from './folder-dialogs.tsx'
import {
  accountDelimiter,
  ancestorPaths,
  folderRefusals,
  folderTree,
  initiallyExpanded,
  subtreeUnread,
  visibleNodes,
  type FolderNode,
  type FolderRefusal,
} from './folder-tree.ts'
import { MAIL_NS } from './i18n.ts'
import { isSameFolder } from './triage-context.ts'

const ROLE_ICONS: Record<FolderRole, LucideIcon> = {
  inbox: Inbox,
  sent: Send,
  drafts: FilePen,
  trash: Trash2,
  junk: ShieldAlert,
  archive: Archive,
  all: Mails,
  flagged: Star,
}

const STORAGE_PREFIX = 'mailroom.mail.expandedFolders.'

/** The levels the user left open on this account, if this browser kept them. */
function storedExpanded(account: string): string[] | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + account)
    const parsed: unknown = raw === null ? null : JSON.parse(raw)
    return Array.isArray(parsed) && parsed.every((path) => typeof path === 'string') ? parsed : null
  } catch {
    return null
  }
}

function storeExpanded(account: string, paths: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + account, JSON.stringify([...paths]))
  } catch {
    // Storage refused (a private window): the tree forgets on reload, nothing else.
  }
}

/**
 * The open levels of `account`'s tree: as this browser kept them, else those above the role
 * folders and the folder shown; the levels above the folder shown, and above `reveal` (a folder
 * just created or renamed), open whenever they change.
 */
function useExpanded(
  account: string,
  folders: readonly MailFolder[],
  current: string | null,
  reveal: string | null,
): [ReadonlySet<string>, (path: string, open: boolean) => void] {
  const delimiter = accountDelimiter(folders)
  const above = (path: string | null): string[] => (path === null ? [] : ancestorPaths(path, delimiter))
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set([...(storedExpanded(account) ?? initiallyExpanded(folders)), ...above(current), ...above(reveal)]),
  )
  const [shownFor, setShownFor] = useState([current, reveal])
  if (shownFor[0] !== current || shownFor[1] !== reveal) {
    setShownFor([current, reveal])
    setExpanded((value) => new Set([...value, ...above(current), ...above(reveal)]))
  }
  const setOpen = (path: string, open: boolean): void => {
    const next = new Set(expanded)
    if (open) {
      next.add(path)
    } else {
      next.delete(path)
    }
    storeExpanded(account, next)
    setExpanded(next)
  }
  return [expanded, setOpen]
}

interface TreeState {
  accountId: string
  current: string | null
  expanded: ReadonlySet<string>
  setOpen: (path: string, open: boolean) => void
  tabStop: string | undefined
  setFocused: (path: string) => void
  /** Opens the actions menu of the folder at `path`; absent where there are none. */
  openMenu: ((path: string) => void) | null
}

/**
 * An account's folders as a tree (the ARIA tree pattern): each level opens and closes, ↑ and ↓
 * move, → and ← open and close, Enter opens the folder. A level that holds only folders is not a
 * link, and a closed one shows the unread count of everything under it. With `onAction`, each
 * listed folder has a menu (its ⋯ button, or Shift+F10 / the context-menu key): a new subfolder,
 * rename or move, delete, each disabled with the reason where the server would refuse it.
 */
export function FolderTree({
  accountId,
  folders,
  current,
  reveal = null,
  label,
  onAction,
}: {
  accountId: string
  folders: readonly MailFolder[]
  current: string | null
  reveal?: string | null
  label: string
  onAction?: (dialog: FolderDialog) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const roots = useMemo(() => folderTree(folders), [folders])
  const [expanded, setOpen] = useExpanded(accountId, folders, current, reveal)
  const [focused, setFocused] = useState<string | null>(null)
  const [menuFor, setMenuFor] = useState<{ node: FolderNode & { folder: MailFolder }; anchor: HTMLElement } | null>(null)
  const closeMenu = useCallback(() => {
    setMenuFor(null)
  }, [])
  const treeRef = useRef<HTMLUListElement>(null)
  const shown = visibleNodes(roots, expanded)
  const tabStop =
    shown.find(({ node }) => node.path === focused)?.node.path ??
    shown.find(({ node }) => current !== null && isSameFolder(node.path, current))?.node.path ??
    shown[0]?.node.path

  function itemAt(path: string): HTMLElement | undefined {
    return [...(treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])].find(
      (element) => element.dataset['path'] === path,
    )
  }

  const openMenu =
    onAction === undefined
      ? null
      : (path: string): void => {
          if (menuFor?.node.path === path) {
            setMenuFor(null)
            return
          }
          const node = shown.find((entry) => entry.node.path === path)?.node
          const anchor = itemAt(path)
          if (node?.folder != null && anchor !== undefined) {
            setMenuFor({ node: { ...node, folder: node.folder }, anchor })
          }
        }

  function focusAt(index: number): void {
    treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]')[index]?.focus()
  }

  function onKeyDown(event: KeyboardEvent<HTMLUListElement>): void {
    if (event.ctrlKey || event.metaKey || event.altKey || !(event.target instanceof Element) || event.target.closest('[role="treeitem"]') === null) {
      return
    }
    const path = event.target.closest('[role="treeitem"]')?.getAttribute('data-path')
    const index = shown.findIndex(({ node }) => node.path === path)
    const entry = shown[index]
    if (entry === undefined) {
      return
    }
    const branch = entry.node.children.length > 0
    const open = branch && expanded.has(entry.node.path)
    if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
      openMenu?.(entry.node.path)
      event.preventDefault()
      return
    }
    switch (event.key) {
      case 'ArrowDown':
        focusAt(index + 1)
        break
      case 'ArrowUp':
        focusAt(index - 1)
        break
      case 'Home':
        focusAt(0)
        break
      case 'End':
        focusAt(shown.length - 1)
        break
      case 'ArrowRight':
        if (open) {
          focusAt(index + 1)
        } else if (branch) {
          setOpen(entry.node.path, true)
        }
        break
      case 'ArrowLeft':
        if (open) {
          setOpen(entry.node.path, false)
        } else if (entry.parent !== null) {
          focusAt(shown.findIndex(({ node }) => node.path === entry.parent))
        }
        break
      case 'Enter':
        // A folder's link opens it; a level that holds only folders opens or closes.
        if (entry.node.folder?.selectable === true || !branch) {
          return
        }
        setOpen(entry.node.path, !open)
        break
      default:
        return
    }
    event.preventDefault()
  }

  const tree: TreeState = { accountId, current, expanded, setOpen, tabStop, setFocused, openMenu }
  return (
    <>
      {/* The keys belong to the treeitems inside; the tree handles them in one place. */}
      <ul ref={treeRef} role="tree" aria-label={label} className="flex flex-col gap-px" onKeyDown={onKeyDown}>
        {roots.map((node) => (
          <TreeItem key={node.path} node={node} level={1} tree={tree} />
        ))}
      </ul>
      {menuFor !== null && onAction !== undefined && (
        <Menu
          label={t('folderActions.menu', { name: menuFor.node.folder.role === null ? menuFor.node.name : t(`roles.${menuFor.node.folder.role}`) })}
          anchor={menuFor.anchor}
          items={actionItems(t, menuFor.node, accountDelimiter(folders) !== null, onAction)}
          onClose={closeMenu}
        />
      )}
    </>
  )
}

const REFUSALS = {
  protected: 'folderActions.protected',
  holdsRole: 'folderActions.holdsRole',
  hasSubfolders: 'folderActions.hasSubfolders',
} as const

function actionItems(
  t: TFunction,
  node: FolderNode & { folder: MailFolder },
  hierarchy: boolean,
  onAction: (dialog: FolderDialog) => void,
): MenuItem[] {
  const refused = folderRefusals(node)
  const reason = (refusal: FolderRefusal): string | null => (refusal === null ? null : t(REFUSALS[refusal]))
  const subfolder: MenuItem = {
    label: t('folderActions.newSubfolder'),
    onSelect: () => {
      onAction({ kind: 'create', parent: node.folder.path })
    },
  }
  return [
    // A server without a hierarchy has no subfolders.
    ...(hierarchy ? [subfolder] : []),
    {
      label: t('folderActions.rename'),
      disabledReason: reason(refused.rename),
      onSelect: () => {
        onAction({ kind: 'rename', folder: node.folder })
      },
    },
    {
      label: t('folderActions.delete'),
      disabledReason: reason(refused.delete),
      destructive: true,
      onSelect: () => {
        onAction({ kind: 'delete', folder: node.folder })
      },
    },
  ]
}

function TreeItem({ node, level, tree }: { node: FolderNode; level: number; tree: TreeState }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const groupId = useId()
  const { folder } = node
  const branch = node.children.length > 0
  const open = branch && tree.expanded.has(node.path)
  const current = folder !== null && tree.current !== null && isSameFolder(folder.path, tree.current)
  const unread = branch && !open ? subtreeUnread(node) : (folder?.unread ?? 0)
  const Icon = !folder?.selectable ? FolderClosed : folder.role === null ? Folder : ROLE_ICONS[folder.role]
  const role = folder?.role ?? null
  const name = role === null ? node.name : t(`roles.${role}`)
  const menu = folder !== null && tree.openMenu !== null

  const content: ReactNode = (
    <>
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {unread > 0 && (
        <span aria-hidden="true" className="text-xs font-medium tabular-nums text-foreground">
          {unread}
        </span>
      )}
    </>
  )
  const tabIndex = node.path === tree.tabStop ? 0 : -1
  const item = {
    // Named here: the group it owns would otherwise join its name.
    'aria-label': unread > 0 ? `${name}, ${t('folders.unread', { count: unread })}` : name,
    'aria-level': level,
    'aria-expanded': branch ? open : undefined,
    'aria-owns': open ? groupId : undefined,
    'aria-haspopup': menu ? 'menu' : undefined,
    'aria-keyshortcuts': menu ? 'Shift+F10' : undefined,
    'data-path': node.path,
    title: node.path,
    onFocus: () => {
      tree.setFocused(node.path)
    },
  } as const
  const itemClass = 'flex min-w-0 flex-1 items-center gap-2 rounded-md py-1 pr-2 pl-1 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring'

  return (
    <li role="none" className="flex flex-col">
      <div
        className={cn(
          'group/row flex items-center rounded-md',
          current ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
        )}
      >
        {branch ? (
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            title={t(open ? 'folders.collapse' : 'folders.expand')}
            // A click leaves focus where it was, in the tree, not on this button.
            onMouseDown={(event) => {
              event.preventDefault()
            }}
            onClick={(event) => {
              // Closing the level that holds the focused folder moves focus to the level.
              const item = event.currentTarget.closest('li')
              if (open && item?.querySelector('[role="group"]')?.contains(document.activeElement) === true) {
                item.querySelector<HTMLElement>('[role="treeitem"]')?.focus()
              }
              tree.setOpen(node.path, !open)
            }}
            className="flex size-5 shrink-0 items-center justify-center rounded-sm hover:text-foreground"
          >
            <ChevronRight aria-hidden="true" className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
          </button>
        ) : (
          <span aria-hidden="true" className="size-5 shrink-0" />
        )}
        {folder?.selectable === true ? (
          <Link
            role="treeitem"
            aria-selected={current}
            tabIndex={tabIndex}
            {...item}
            to="/mail"
            search={{ account: tree.accountId, folder: folder.path }}
            aria-current={current ? 'page' : undefined}
            className={itemClass}
          >
            {content}
          </Link>
        ) : (
          // Enter opens and closes it through the tree's keys.
          // eslint-disable-next-line jsx-a11y/click-events-have-key-events
          <span
            role="treeitem"
            aria-selected={false}
            tabIndex={tabIndex}
            {...item}
            onClick={() => {
              if (branch) {
                tree.setOpen(node.path, !open)
              }
            }}
            className={cn(itemClass, 'cursor-default opacity-80')}
          >
            {content}
          </span>
        )}
        {menu && (
          // Pointer only: from the keyboard, Shift+F10 on the folder opens the same menu.
          <button
            type="button"
            tabIndex={-1}
            aria-hidden="true"
            title={t('folderActions.more')}
            // Focus stays in the tree, and the menu's click-outside does not see this press: a
            // second click closes the menu instead of reopening it.
            onMouseDown={(event) => {
              event.preventDefault()
            }}
            onPointerDown={(event) => {
              event.stopPropagation()
            }}
            onClick={() => {
              tree.openMenu?.(node.path)
            }}
            className="mr-0.5 flex size-6 shrink-0 items-center justify-center rounded-sm opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 hover:bg-accent hover:text-foreground"
          >
            <MoreHorizontal aria-hidden="true" className="size-4" />
          </button>
        )}
      </div>
      {open && (
        <ul role="group" id={groupId} className="ml-2.5 flex flex-col gap-px border-l border-border pl-1">
          {node.children.map((child) => (
            <TreeItem key={child.path} node={child} level={level + 1} tree={tree} />
          ))}
        </ul>
      )}
    </li>
  )
}
