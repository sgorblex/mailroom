import { useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { Bookmark, Folders, MoreHorizontal, Search } from 'lucide-react'

import type { Account, AccountId } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import {
  ALL_INBOXES,
  createView,
  deleteView,
  renameView,
  reorderViews,
  type SavedView,
  type SearchQuery,
  type ViewKey,
  type ViewPage,
} from '@/api/mail.ts'
import { useAccountName } from '@/accounts/accounts-context.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'
import { Menu, type MenuItem } from '@/components/ui/menu.tsx'
import { cn } from '@/lib/utils.ts'

import { Buttons } from './folder-dialogs.tsx'
import { MAIL_NS } from './i18n.ts'
import { mailErrorMessage } from './messages.ts'
import { messagesKey, READ_ON_MOUNT, useListedFolders, VIEWS_KEY, viewsQuery } from './queries.ts'
import { ALL_INBOXES_ICON } from './role-icons.ts'
import { savableLine, viewUnread, type ShownView } from './views.ts'

/** As the server keeps them: a name of 1-100 characters, trimmed. */
const MAX_VIEW_NAME = 100

/** A refused save of a search as a view (`save`), or change to a view (`change`: a rename, a delete, a reorder), in the reader's language. */
function viewErrorMessage(t: TFunction, failure: unknown, action: 'save' | 'change'): string {
  if (failure instanceof ApiRequestError) {
    switch (failure.code) {
      case 'view_name_taken':
        return t('views.errors.nameTaken')
      case 'too_many_views':
        return t('views.errors.tooMany')
      case 'invalid_argument':
        return t(action === 'save' ? 'views.errors.invalid' : 'views.errors.nameInvalid')
      case 'not_found':
        return t('views.notFound')
    }
  }
  return mailErrorMessage(t, failure, t(action === 'save' ? 'views.errors.failed' : 'views.errors.changeFailed'))
}

type ViewDialog = { kind: 'rename'; view: SavedView } | { kind: 'delete'; view: SavedView }

/**
 * The sidebar's views: *All inboxes* and the user's saved views, each opening its list, with the
 * unread count of its folders where they are listed already. Each saved view's menu renames,
 * moves or deletes it.
 */
export function ViewsSection({
  accounts,
  accountId,
  current,
}: {
  accounts: Account[]
  /** The account shown, kept in the URL while a view is. */
  accountId: AccountId
  current: ViewKey | null
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const headingId = useId()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const views = useQuery(viewsQuery())
  const ids = accounts.map((account) => account.id)
  const listed = useListedFolders(ids)
  const [menu, setMenu] = useState<{ view: SavedView; anchor: HTMLElement } | null>(null)
  const [dialog, setDialog] = useState<ViewDialog | null>(null)
  const [notice, setNotice] = useState<{ text: string; alert: boolean } | null>(null)
  const saved = views.data ?? []

  async function move(view: SavedView, by: -1 | 1): Promise<void> {
    const order = saved.map((each) => each.id)
    const at = order.indexOf(view.id)
    const other = order[at + by]
    if (other === undefined) {
      return
    }
    order[at + by] = view.id
    order[at] = other
    setNotice(null)
    try {
      queryClient.setQueryData(VIEWS_KEY, await reorderViews(order))
    } catch (error) {
      setNotice({ text: viewErrorMessage(t, error, 'change'), alert: true })
      void views.refetch()
    }
  }

  function menuItems(view: SavedView): MenuItem[] {
    const at = saved.findIndex((each) => each.id === view.id)
    const items: MenuItem[] = [
      {
        label: t('views.rename'),
        onSelect: () => {
          setDialog({ kind: 'rename', view })
        },
      },
    ]
    if (at > 0) {
      items.push({ label: t('views.moveUp'), onSelect: () => void move(view, -1) })
    }
    if (at < saved.length - 1) {
      items.push({ label: t('views.moveDown'), onSelect: () => void move(view, 1) })
    }
    items.push({
      label: t('views.delete'),
      destructive: true,
      onSelect: () => {
        setDialog({ kind: 'delete', view })
      },
    })
    return items
  }

  function done(kind: ViewDialog['kind'], view: SavedView): void {
    setDialog(null)
    if (kind === 'rename') {
      queryClient.setQueryData<SavedView[]>(VIEWS_KEY, (old) => old?.map((each) => (each.id === view.id ? view : each)))
      setNotice({ text: t('views.renamed', { name: view.name }), alert: false })
      return
    }
    queryClient.setQueryData<SavedView[]>(VIEWS_KEY, (old) => old?.filter((each) => each.id !== view.id))
    setNotice({ text: t('views.deleted', { name: view.name }), alert: false })
    if (current === view.id) {
      void navigate({ to: '/mail', search: { account: accountId } })
    }
  }

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1">
      <h2 id={headingId} className="px-2 text-sm font-medium">
        {t('views.title')}
      </h2>
      <ul aria-labelledby={headingId} className="flex flex-col gap-px">
        <ViewRow
          view={ALL_INBOXES}
          name={t('views.allInboxes')}
          accountId={accountId}
          current={current === ALL_INBOXES}
          unread={viewUnread(ALL_INBOXES, ids, listed)}
        />
        {saved.map((view) => (
          <ViewRow
            key={view.id}
            view={view}
            name={view.name}
            accountId={accountId}
            current={current === view.id}
            unread={viewUnread(view, ids, listed)}
            onMenu={(anchor) => {
              setNotice(null)
              setMenu({ view, anchor })
            }}
          />
        ))}
      </ul>
      {views.status === 'error' && (
        <div className="flex flex-col items-start gap-2 px-2">
          <p role="alert" className="text-sm">
            {mailErrorMessage(t, views.error, t('views.loadFailed'))}
          </p>
          <Button variant="outline" size="sm" onClick={() => void views.refetch()}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      <div role="status" className="px-2 text-xs text-muted-foreground empty:hidden">
        {notice !== null && !notice.alert ? notice.text : null}
      </div>
      {notice?.alert === true && (
        <p role="alert" className="px-2 text-xs">
          {notice.text}
        </p>
      )}
      {menu !== null && (
        <Menu
          label={t('views.actions', { name: menu.view.name })}
          anchor={menu.anchor}
          items={menuItems(menu.view)}
          onClose={() => {
            setMenu(null)
          }}
        />
      )}
      {dialog?.kind === 'rename' && (
        <RenameViewDialog
          view={dialog.view}
          onCancel={() => {
            setDialog(null)
          }}
          onDone={(view) => {
            done('rename', view)
          }}
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteViewDialog
          view={dialog.view}
          onCancel={() => {
            setDialog(null)
          }}
          onDone={() => {
            done('delete', dialog.view)
          }}
        />
      )}
    </section>
  )
}

function ViewRow({
  view,
  name,
  accountId,
  current,
  unread,
  onMenu,
}: {
  view: ShownView
  name: string
  accountId: AccountId
  current: boolean
  unread: number | null
  /** Opens the view's menu under the given button; none for *All inboxes*. */
  onMenu?: (anchor: HTMLElement) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const key: ViewKey = view === ALL_INBOXES ? ALL_INBOXES : view.id
  const Icon = view === ALL_INBOXES ? ALL_INBOXES_ICON : view.query === null ? Folders : Search
  const counted = unread !== null && unread > 0
  return (
    <li
      className={cn(
        'group/row flex items-center rounded-md',
        current ? 'bg-accent font-medium text-foreground' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      <Link
        to="/mail"
        search={{ account: accountId, view: key }}
        aria-current={current ? 'page' : undefined}
        aria-label={counted ? `${name}, ${t('folders.unread', { count: unread })}` : name}
        className="flex min-w-0 flex-1 items-center gap-2 rounded-md py-1 pr-2 pl-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Icon aria-hidden="true" className="size-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate">{name}</span>
        {counted && (
          <span aria-hidden="true" className="text-xs font-medium tabular-nums text-foreground">
            {unread}
          </span>
        )}
      </Link>
      {onMenu !== undefined && (
        <button
          type="button"
          aria-label={t('views.actions', { name })}
          aria-haspopup="menu"
          title={t('views.more')}
          // The menu's click-outside does not see this press: a second click closes the menu.
          onPointerDown={(event) => {
            event.stopPropagation()
          }}
          onClick={(event) => {
            onMenu(event.currentTarget)
          }}
          className="mr-0.5 flex size-6 shrink-0 items-center justify-center rounded-sm opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 focus-visible:opacity-100 hover:bg-accent hover:text-foreground pointer-coarse:opacity-100"
        >
          <MoreHorizontal aria-hidden="true" className="size-4" />
        </button>
      )}
    </li>
  )
}

/** The name field of a view's dialog, with what is wrong with it once the user tried to save. */
function NameField({ id, value, error, onChange }: { id: string; value: string; error: string | null; onChange: (value: string) => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {t('views.name')}
      </label>
      <Input
        id={id}
        value={value}
        maxLength={MAX_VIEW_NAME}
        autoComplete="off"
        aria-invalid={error !== null || undefined}
        aria-describedby={error === null ? undefined : `${id}-error`}
        onChange={(event) => {
          onChange(event.target.value)
        }}
      />
      {error !== null && (
        <p id={`${id}-error`} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

/** A name dialog's form: the trimmed name sent to `save`, a refusal said in it. */
function useNameForm(
  initial: string,
  action: 'save' | 'change',
  save: (name: string) => Promise<void>,
): { id: string; name: string; setName: (name: string) => void; error: string | null; failure: string | null; busy: boolean; submit: (event: SubmitEvent<HTMLFormElement>) => void } {
  const { t } = useTranslation(MAIL_NS)
  const id = useId()
  const [name, setName] = useState(initial)
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const missing = name.trim() === ''
  const submit = (event: SubmitEvent<HTMLFormElement>): void => {
    event.preventDefault()
    // Not the search form's, which a dialog opened from it is portalled out of.
    event.stopPropagation()
    setTouched(true)
    if (missing) {
      document.getElementById(id)?.focus()
      return
    }
    if (busy) {
      return
    }
    setBusy(true)
    setFailure(null)
    save(name.trim()).catch((error: unknown) => {
      setFailure(viewErrorMessage(t, error, action))
      setBusy(false)
    })
  }
  return { id, name, setName, error: touched && missing ? t('views.errors.nameMissing') : null, failure, busy, submit }
}

function Failure({ text }: { text: string | null }): ReactElement | null {
  return text === null ? null : (
    <p role="alert" className="text-sm text-destructive">
      {text}
    </p>
  )
}

function RenameViewDialog({ view, onCancel, onDone }: { view: SavedView; onCancel: () => void; onDone: (view: SavedView) => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const form = useNameForm(view.name, 'change', async (name) => {
    onDone(name === view.name ? view : await renameView(view.id, name))
  })
  const title = t('views.renameTitle', { name: view.name })
  return (
    <Dialog label={title} onCancel={onCancel}>
      <form noValidate onSubmit={form.submit} className="flex w-80 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        <NameField id={form.id} value={form.name} error={form.error} onChange={form.setName} />
        <Failure text={form.failure} />
        <Buttons cancel={t('views.cancel')} confirm={t('views.renameConfirm')} disabled={form.busy} onCancel={onCancel} />
      </form>
    </Dialog>
  )
}

function DeleteViewDialog({ view, onCancel, onDone }: { view: SavedView; onCancel: () => void; onDone: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const cancel = useRef<HTMLButtonElement>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const title = t('views.deleteTitle', { name: view.name })

  function submit(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (busy) {
      return
    }
    setBusy(true)
    setFailure(null)
    deleteView(view.id).then(onDone, (error: unknown) => {
      setFailure(viewErrorMessage(t, error, 'change'))
      setBusy(false)
    })
  }

  return (
    <Dialog label={title} onCancel={onCancel}>
      <form noValidate onSubmit={submit} className="flex w-80 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        <p className="text-sm text-muted-foreground">{t('views.deleteNote')}</p>
        <Failure text={failure} />
        <Buttons
          cancel={t('views.cancel')}
          confirm={t('views.deleteConfirm')}
          disabled={busy}
          destructive
          onCancel={onCancel}
          cancelRef={cancel}
        />
      </form>
    </Dialog>
  )
}

/**
 * "Save as view" for the search shown: named in a dialog, kept as its line, then opened. A search
 * for words or with Gmail's syntax cannot be one, the index searching neither: the button says so.
 */
export function SaveViewButton({ query, className }: { query: SearchQuery; className?: string }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [open, setOpen] = useState(false)
  const line = savableLine(query)
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="xs"
        className={cn('aria-disabled:opacity-50', className)}
        aria-disabled={line === null}
        title={line === null ? t('views.cannotSave') : undefined}
        onClick={() => {
          if (line !== null) {
            setOpen(true)
          }
        }}
      >
        <Bookmark aria-hidden="true" />
        {t('views.save')}
      </Button>
      {/* Out of the search form: a form holds no other form. */}
      {open &&
        line !== null &&
        createPortal(
          <SaveViewDialog
            line={line}
            onCancel={() => {
              setOpen(false)
            }}
          />,
          document.body,
        )}
    </>
  )
}

function SaveViewDialog({ line, onCancel }: { line: string; onCancel: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const form = useNameForm('', 'save', async (name) => {
    const view = await createView(name, line)
    queryClient.setQueryData<SavedView[]>(VIEWS_KEY, (old) => (old === undefined ? undefined : [...old, view]))
    void queryClient.invalidateQueries({ queryKey: VIEWS_KEY })
    void navigate({ to: '/mail', search: (previous) => ({ account: previous.account, view: view.id }) })
  })
  const title = t('views.saveTitle')
  return (
    <Dialog label={title} onCancel={onCancel}>
      <form noValidate onSubmit={form.submit} className="flex w-80 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        <NameField id={form.id} value={form.name} error={form.error} onChange={form.setName} />
        <p className="text-sm text-muted-foreground">{t('views.saveNote')}</p>
        <Failure text={form.failure} />
        <Buttons cancel={t('views.cancel')} confirm={t('views.saveConfirm')} disabled={form.busy} onCancel={onCancel} />
      </form>
    </Dialog>
  )
}

/**
 * The view's folders its first page left out, off the index or not ready yet: read from that
 * page as the list loads it (`source`, `loadPage`).
 */
export function NotIndexedNotice({
  source,
  loadPage,
}: {
  source: string
  loadPage: (cursor: string | null, signal: AbortSignal) => Promise<ViewPage>
}): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const accountName = useAccountName()
  const first = useQuery({
    queryKey: messagesKey(source, null),
    queryFn: ({ signal }) => loadPage(null, signal),
    // The list's triage and live merges may write the cached page again without it.
    select: (page: Partial<ViewPage>) => page.not_indexed ?? [],
    ...READ_ON_MOUNT,
  })
  const left = first.data ?? []
  if (left.length === 0) {
    return null
  }
  const folders = left
    .map((each) => `${accountName(each.account_id)}: ${each.folder === '*' ? t('views.everyFolder') : each.folder}`)
    .join('; ')
  return (
    <p role="status" className="text-xs text-muted-foreground">
      {t('views.notIndexed', { count: left.length, folders })}
    </p>
  )
}
