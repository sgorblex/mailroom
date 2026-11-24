import { createElement, useEffect, useRef, useState, type MouseEvent, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Menu as MenuIcon, Search, X } from 'lucide-react'

import type { Account, AccountId } from '@/api/accounts.ts'
import { ALL_INBOXES, type ViewKey } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'

import { useComposeAction } from './compose-action.ts'
import { MAIL_NS } from './i18n.ts'
import { MailSidebar } from './mail-sidebar.tsx'
import { folderName } from './messages.ts'
import { viewsQuery } from './queries.ts'
import { isSameFolder, useAccountFolders } from './triage-context.ts'

/**
 * The Mail header on a narrow screen (below `lg`, where the folder pane is no column): ☰ opening
 * the folders drawer, the name of what the list shows, a button showing the search form
 * (`searchId`) and one starting a message. `hidden` while a message or the composer takes the screen,
 * which closes the drawer too (Edit in its Scheduled list opens the composer, for one).
 */
export function MailHeader({
  accounts,
  accountId,
  folder,
  view,
  subfolders,
  searching,
  searchId,
  searchShown,
  onSearch,
  hidden,
}: {
  accounts: Account[]
  accountId: AccountId
  /** The folder shown, `null` while a search or a view is. */
  folder: string | null
  view: ViewKey | null
  subfolders: boolean
  /** The list shows a search's results. */
  searching: boolean
  searchId: string
  searchShown: boolean
  onSearch: () => void
  hidden: boolean
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [drawer, setDrawer] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  // Closed by the user, the drawer gives the focus back to ☰ once gone: until then the modal keeps
  // the page behind, ☰ included, out of reach of focus.
  const refocus = useRef(false)
  useEffect(() => {
    if (!drawer && refocus.current) {
      refocus.current = false
      menuButton.current?.focus()
    }
  }, [drawer])
  const title = useShownTitle(accountId, folder, view, searching)
  // Left open, the modal drawer would keep the screen that took its place out of reach.
  if (hidden && drawer) {
    setDrawer(false)
  }
  return (
    <>
      <header hidden={hidden} className="sticky top-(--deploy-banner) z-20 flex items-center gap-1 border-b bg-background px-2 py-1.5">
      <Button
        ref={menuButton}
        type="button"
        variant="ghost"
        size="icon"
        aria-label={t('mobile.folders')}
        aria-haspopup="dialog"
        aria-expanded={drawer}
        onClick={() => {
          setDrawer(true)
        }}
      >
        <MenuIcon aria-hidden="true" />
      </Button>
      <h2 className="min-w-0 flex-1 truncate px-1 text-base font-semibold">{title}</h2>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={t('mobile.search')}
        aria-expanded={searchShown}
        aria-controls={searchId}
        onClick={onSearch}
      >
        <Search aria-hidden="true" />
      </Button>
      <ComposeButton accountId={accountId} />
      </header>
      {/* Not within the header, which `hidden` takes off the screen. */}
      {drawer && !hidden && (
        <FolderDrawer
          sidebar={{ accounts, accountId, folder, view, subfolders }}
          onClose={() => {
            refocus.current = true
            setDrawer(false)
          }}
        />
      )}
    </>
  )
}

/** What the list shows, named: the search's results, a view, or the folder. */
function useShownTitle(accountId: AccountId, folder: string | null, view: ViewKey | null, searching: boolean): string {
  const { t } = useTranslation(MAIL_NS)
  const saved = useQuery(viewsQuery()).data
  // Read here too: on a narrow screen the folder pane is mounted only in its drawer.
  const { state } = useAccountFolders(accountId)
  const folders = state.kind === 'loaded' ? state.value : undefined
  if (searching) {
    return t('list.results')
  }
  if (view !== null) {
    return view === ALL_INBOXES ? t('views.allInboxes') : (saved?.find((each) => each.id === view)?.name ?? t('mail.title'))
  }
  if (folder === null) {
    return t('mail.title')
  }
  const found = folders?.find((each) => isSameFolder(each.path, folder))
  return found === undefined ? folder : folderName(t, found)
}

/** Starts a message on the account shown, as `c` does; focus comes back to it once the composer closes. */
function ComposeButton({ accountId }: { accountId: AccountId }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { action, context, reason, run, button } = useComposeAction(accountId)
  const label = t(action.label(context))
  return (
    <Button
      ref={button}
      type="button"
      size="icon"
      aria-label={label}
      title={reason === null ? `${label} (${action.shortcut ?? ''})` : t(reason)}
      aria-keyshortcuts={action.shortcut}
      aria-disabled={reason !== null}
      onClick={run}
    >
      {createElement(action.icon(context), { 'aria-hidden': true })}
    </Button>
  )
}

/**
 * The folder pane in a drawer from the left: a modal dialog, which keeps focus within it and the
 * page behind out of reach. Escape, the scrim, its Close button or a folder or view picked (a link
 * followed) closes it; `onClose` gives the focus back.
 */
function FolderDrawer({
  sidebar,
  onClose,
}: {
  sidebar: { accounts: Account[]; accountId: AccountId; folder: string | null; view: ViewKey | null; subfolders: boolean }
  onClose: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)

  function onClick(event: MouseEvent<HTMLDialogElement>): void {
    const drawer = event.currentTarget
    // The scrim is the dialog itself, which its content fills.
    if (event.target === drawer) {
      onClose()
      return
    }
    const link = event.target instanceof Element ? event.target.closest('a[href]') : null
    // A link of the pane, not one in a dialog opened from it.
    if (link !== null && link.closest('dialog') === drawer) {
      onClose()
    }
  }

  return (
    <Dialog
      label={t('mobile.folders')}
      onCancel={onClose}
      onClick={onClick}
      className="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-80 max-w-[85vw] rounded-none border-0 border-r p-0 shadow-lg transition-[translate] duration-200 starting:open:-translate-x-full motion-reduce:transition-none"
    >
      <div className="flex h-full flex-col overflow-y-auto pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
        <div className="flex items-center gap-1 px-3 pt-2">
          <p className="min-w-0 flex-1 px-2 text-base font-semibold">{t('folders.title')}</p>
          <Button type="button" variant="ghost" size="icon" aria-label={t('mobile.closeFolders')} onClick={onClose}>
            <X aria-hidden="true" />
          </Button>
        </div>
        <MailSidebar {...sidebar} />
      </div>
    </Dialog>
  )
}
