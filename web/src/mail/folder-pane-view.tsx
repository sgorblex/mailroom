import { useCallback, useEffect, useId, useRef, useState, type FocusEvent, type ReactElement, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from '@tanstack/react-router'
import { PanelLeftClose, PanelLeftOpen, type LucideIcon } from 'lucide-react'

import type { Account, AccountId } from '@/api/accounts.ts'
import { ALL_INBOXES, type FolderRole, type ViewKey } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { useFolderPane } from './folder-pane.ts'
import { MAIL_NS } from './i18n.ts'
import { MailSidebar } from './mail-sidebar.tsx'
import { useListedFolders } from './queries.ts'
import { ALL_INBOXES_ICON, ROLE_ICONS } from './role-icons.ts'
import { isTyping, overlayOpen } from './shortcuts.ts'
import { isSameFolder, loadedFolders, roleFolder, useTriage } from './triage-context.ts'
import { viewUnread } from './views.ts'

/** The role folders the rail shows, those the account has, in this order. */
const RAIL_ROLES: readonly FolderRole[] = ['inbox', 'flagged', 'drafts', 'sent', 'archive', 'junk', 'trash']

/**
 * The folder pane (`MailSidebar`), which a button or `[` folds to a rail of icons on a wide screen,
 * this device's choice; folded, hovering or focusing the rail peeks the whole pane over the list,
 * taking no room, until the pointer or the focus leaves it, Escape, or a folder is picked.
 */
export function FolderPane({
  accounts,
  accountId,
  folder,
  view,
  subfolders,
  className,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string | null
  view: ViewKey | null
  subfolders: boolean
  className?: string
}): ReactElement {
  const { collapsed, wide, toggle } = useFolderPane()
  const [peek, setPeek] = useState(false)
  // Closed by Escape or a pick: focus moving within does not open it again until it has left.
  const dismissed = useRef(false)
  const wrapper = useRef<HTMLDivElement>(null)
  const toggleButton = useRef<HTMLButtonElement>(null)
  // Pressed with the button, which goes: the focus moves to the one taking its place.
  const refocus = useRef(false)
  // The element last focused in the pane, if the focus is there: folded or opened (by `[` too),
  // the pane is drawn anew and the focus would fall to the page.
  const focusedIn = useRef<Element | null>(null)
  // The pointer left while a menu or a dialog was open: the peek closes once it moves, they closed.
  const pointerLeft = useRef(false)
  const paneId = useId()
  if (peek && !collapsed) {
    setPeek(false)
  }

  useEffect(() => {
    function onFocusIn(event: globalThis.FocusEvent): void {
      const target = event.target
      const pane = wrapper.current ?? document.getElementById(paneId)
      focusedIn.current = target instanceof Element && pane?.contains(target) === true ? target : null
    }
    document.addEventListener('focusin', onFocusIn)
    return () => {
      document.removeEventListener('focusin', onFocusIn)
    }
  }, [paneId])

  useEffect(() => {
    const active = document.activeElement
    const lost = (active === null || active === document.body) && focusedIn.current?.isConnected === false
    if (refocus.current || lost) {
      refocus.current = false
      dismissed.current = true
      toggleButton.current?.focus()
    }
  }, [collapsed])

  /** Hides the peek; the focus in it goes to the toggle, which leaves it closed. */
  const close = useCallback((): void => {
    pointerLeft.current = false
    dismissed.current = false
    setPeek(false)
    if (document.getElementById(paneId)?.contains(document.activeElement) === true) {
      dismissed.current = true
      toggleButton.current?.focus()
    }
  }, [paneId])

  // A folder or a view picked: the list shows it, and the pane gets out of the way.
  const shown = `${String(accountId)}:${folder ?? ''}:${String(view ?? '')}`
  const shownBefore = useRef(shown)
  useEffect(() => {
    if (shownBefore.current === shown) {
      return
    }
    shownBefore.current = shown
    setPeek(false)
    if (wrapper.current?.contains(document.activeElement) === true) {
      dismissed.current = true
      toggleButton.current?.focus()
    }
  }, [shown])

  useEffect(() => {
    if (!peek) {
      return
    }
    // Before the screen's own Escape (which clears the selection), wherever the focus is but in a
    // field elsewhere, whose Escape is its own.
    function onKeyDown(event: KeyboardEvent): void {
      const target = event.target
      if (
        event.key !== 'Escape' ||
        event.defaultPrevented ||
        overlayOpen() ||
        (isTyping(target) && !(target instanceof Node && wrapper.current?.contains(target) === true))
      ) {
        return
      }
      event.preventDefault()
      dismissed.current = true
      setPeek(false)
      if (wrapper.current?.contains(document.activeElement) === true) {
        toggleButton.current?.focus()
      }
    }
    function onPointerMove(event: PointerEvent): void {
      const target = event.target
      if (!pointerLeft.current || overlayOpen() || (target instanceof Node && wrapper.current?.contains(target) === true)) {
        return
      }
      close()
    }
    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('pointermove', onPointerMove)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('pointermove', onPointerMove)
    }
  }, [peek, close])

  const sidebar = { accounts, accountId, folder, view, subfolders }
  const pressToggle = (): void => {
    refocus.current = true
    toggle()
  }

  if (!collapsed) {
    return (
      <MailSidebar
        {...sidebar}
        id={paneId}
        className={className}
        header={
          wide ? <PaneToggle collapsed={false} controls={paneId} onToggle={pressToggle} button={toggleButton} /> : undefined
        }
      />
    )
  }

  return (
    <div
      ref={wrapper}
      className={cn('relative flex shrink-0', className)}
      onMouseEnter={() => {
        pointerLeft.current = false
        dismissed.current = false
        setPeek(true)
      }}
      onMouseLeave={() => {
        if (overlayOpen()) {
          pointerLeft.current = true
        } else {
          close()
        }
      }}
      onFocus={() => {
        if (!dismissed.current) {
          setPeek(true)
        }
      }}
      onBlur={(event: FocusEvent<HTMLDivElement>) => {
        const to = event.relatedTarget
        if (!(to instanceof Node && event.currentTarget.contains(to)) && !overlayOpen()) {
          close()
        }
      }}
    >
      <FolderRail accounts={accounts} accountId={accountId} folder={folder} view={view}>
        <PaneToggle collapsed controls={paneId} onToggle={pressToggle} button={toggleButton} />
      </FolderRail>
      <MailSidebar
        {...sidebar}
        id={paneId}
        hidden={!peek}
        className="absolute inset-y-0 left-full z-30 w-60 overflow-y-auto border-r bg-background shadow-lg"
      />
    </div>
  )
}

/** Folds the pane to its rail, or opens it again; `[` does the same. */
function PaneToggle({
  collapsed,
  controls,
  onToggle,
  button,
}: {
  collapsed: boolean
  /** The id of the pane it folds. */
  controls: string
  onToggle: () => void
  button: RefObject<HTMLButtonElement | null>
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const label = collapsed ? t('folderPane.expand') : t('folderPane.collapse')
  const Icon = collapsed ? PanelLeftOpen : PanelLeftClose
  return (
    <Button
      ref={button}
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={`${label} ([)`}
      aria-keyshortcuts="["
      aria-expanded={!collapsed}
      aria-controls={controls}
      className={collapsed ? undefined : 'self-end'}
      onClick={onToggle}
    >
      <Icon aria-hidden="true" />
    </Button>
  )
}

/** The folded pane: the toggle (`children`), *All inboxes* and the account's role folders, each with a dot while it has unread mail. */
function FolderRail({
  accounts,
  accountId,
  folder,
  view,
  children,
}: {
  accounts: Account[]
  accountId: AccountId
  folder: string | null
  view: ViewKey | null
  children: ReactElement
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const ids = accounts.map((account) => account.id)
  const listed = useListedFolders(ids)
  const folders = loadedFolders(useTriage().folders[accountId])
  const roles = RAIL_ROLES.flatMap((role) => {
    const found = roleFolder(folders, role)
    return found === undefined ? [] : [{ role, folder: found }]
  })
  return (
    <nav aria-label={t('folderPane.rail')} className="flex w-14 flex-col items-center gap-1 overflow-y-auto border-r p-2">
      {children}
      <RailLink
        icon={ALL_INBOXES_ICON}
        name={t('views.allInboxes')}
        unread={viewUnread(ALL_INBOXES, ids, listed) ?? 0}
        current={view === ALL_INBOXES}
        search={{ account: accountId, view: ALL_INBOXES }}
      />
      {roles.map(({ role, folder: shown }) => (
        <RailLink
          key={role}
          icon={ROLE_ICONS[role]}
          name={t(`roles.${role}`)}
          unread={shown.unread}
          current={view === null && folder !== null && isSameFolder(folder, shown.path)}
          search={{ account: accountId, folder: shown.path }}
        />
      ))}
    </nav>
  )
}

function RailLink({
  icon: Icon,
  name,
  unread,
  current,
  search,
}: {
  icon: LucideIcon
  name: string
  unread: number
  current: boolean
  search: { account: AccountId; folder?: string; view?: ViewKey }
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const label = unread > 0 ? `${name}, ${t('folders.unread', { count: unread })}` : name
  return (
    <Link
      to="/mail"
      search={search}
      aria-current={current ? 'page' : undefined}
      aria-label={label}
      title={label}
      className={cn(
        'relative flex size-9 shrink-0 items-center justify-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring',
        current ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      <Icon aria-hidden="true" className="size-4" />
      {unread > 0 && <span aria-hidden="true" className="absolute top-1.5 right-1.5 size-2 rounded-full bg-primary" />}
    </Link>
  )
}
