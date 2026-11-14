import { useEffect, useId, useRef, useState, type ReactElement, type Ref, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Link } from '@tanstack/react-router'

import { createFolder, deleteFolder, listMailFolders, renameFolder, type FolderRenamed, type MailFolder } from '@/api/mail.ts'
import { ApiRequestError } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'

import { accountDelimiter, ancestorPaths, isWithin, MAX_FOLDER_NAME, nameProblem, pathSegments } from './folder-tree.ts'
import { MAIL_NS } from './i18n.ts'
import { folderName, mailErrorMessage } from './messages.ts'
import { isSameFolder, roleFolder } from './triage-context.ts'

/** The folder action whose dialog is open. */
export type FolderDialog =
  | { kind: 'create'; parent: string | null }
  | { kind: 'rename'; folder: MailFolder }
  | { kind: 'delete'; folder: MailFolder }

/** What a dialog did, for the sidebar to follow. */
export type FolderDone =
  | { kind: 'created'; path: string }
  | { kind: 'renamed'; answer: FolderRenamed }
  | { kind: 'deleted'; path: string }

export function FolderDialogs({
  accountId,
  folders,
  dialog,
  onCancel,
  onDone,
}: {
  accountId: string
  folders: readonly MailFolder[]
  dialog: FolderDialog
  onCancel: () => void
  onDone: (done: FolderDone) => void
}): ReactElement {
  const delimiter = accountDelimiter(folders)
  switch (dialog.kind) {
    case 'create':
      return <CreateDialog accountId={accountId} parent={dialog.parent} delimiter={delimiter} onCancel={onCancel} onDone={onDone} />
    case 'rename':
      return (
        <RenameDialog
          accountId={accountId}
          folders={folders}
          folder={dialog.folder}
          delimiter={delimiter}
          onCancel={onCancel}
          onDone={onDone}
        />
      )
    case 'delete':
      return (
        <DeleteDialog
          accountId={accountId}
          folder={dialog.folder}
          gmail={roleFolder([...folders], 'all') !== undefined}
          onCancel={onCancel}
          onDone={onDone}
        />
      )
  }
}

/** The last segment of a folder's path: what a rename starts from and a dialog calls it. */
function ownName(path: string, delimiter: string | null): string {
  return pathSegments(path, delimiter).at(-1) ?? path
}

function nameError(t: TFunction, name: string, delimiter: string | null, parent: string | null): string | null {
  const problem = nameProblem(name, delimiter, parent)
  if (problem === null) {
    return null
  }
  if (problem === 'characters') {
    return delimiter === null || delimiter === ''
      ? t('folderActions.problems.charactersNoDelimiter')
      : t('folderActions.problems.characters', { delimiter })
  }
  return t(`folderActions.problems.${problem}`)
}

/** Cancel, then the action: opening the dialog focuses Cancel where no field comes first. */
export function Buttons({
  cancel,
  confirm,
  disabled,
  destructive = false,
  onCancel,
  cancelRef,
}: {
  cancel: string
  confirm: string | null
  disabled: boolean
  destructive?: boolean
  onCancel: () => void
  cancelRef?: Ref<HTMLButtonElement>
}): ReactElement {
  return (
    <div className="mt-4 flex justify-end gap-2">
      <Button ref={cancelRef} type="button" variant="ghost" size="sm" onClick={onCancel}>
        {cancel}
      </Button>
      {confirm !== null && (
        <Button type="submit" variant={destructive ? 'destructive' : 'default'} size="sm" disabled={disabled}>
          {confirm}
        </Button>
      )}
    </div>
  )
}

function NameField({
  id,
  value,
  error,
  onChange,
}: {
  id: string
  value: string
  error: string | null
  onChange: (value: string) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {t('folderActions.name')}
      </label>
      <Input
        id={id}
        value={value}
        maxLength={MAX_FOLDER_NAME + 1}
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

function CreateDialog({
  accountId,
  parent,
  delimiter,
  onCancel,
  onDone,
}: {
  accountId: string
  parent: string | null
  delimiter: string | null
  onCancel: () => void
  onDone: (done: FolderDone) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const id = useId()
  const [name, setName] = useState('')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const problem = nameError(t, name, delimiter, parent)

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setTouched(true)
    if (problem !== null) {
      // The field says what is wrong; a reader hears it there.
      document.getElementById(id)?.focus()
      return
    }
    if (busy) {
      return
    }
    setBusy(true)
    setFailure(null)
    try {
      onDone({ kind: 'created', path: await createFolder(accountId, parent, name) })
    } catch (error) {
      setFailure(mailErrorMessage(t, error, t('folderActions.failed')))
      setBusy(false)
    }
  }

  return (
    <Dialog label={t('folderActions.createTitle')} onCancel={onCancel}>
      <form noValidate onSubmit={(event) => void submit(event)} className="flex w-80 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{t('folderActions.createTitle')}</h2>
        <p className="text-sm text-muted-foreground">
          {parent === null ? t('folderActions.atTop', { id: accountId }) : t('folderActions.inside', { parent })}
        </p>
        <NameField id={id} value={name} error={touched ? problem : null} onChange={setName} />
        {failure !== null && (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <Buttons cancel={t('folderActions.cancel')} confirm={t('folderActions.create')} disabled={busy} onCancel={onCancel} />
      </form>
    </Dialog>
  )
}

const TOP_LEVEL = ''

function RenameDialog({
  accountId,
  folders,
  folder,
  delimiter,
  onCancel,
  onDone,
}: {
  accountId: string
  folders: readonly MailFolder[]
  folder: MailFolder
  delimiter: string | null
  onCancel: () => void
  onDone: (done: FolderDone) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const id = useId()
  const parentId = useId()
  const current = folder.path
  const currentParent = ancestorPaths(current, delimiter).at(-1) ?? null
  const [name, setName] = useState(ownName(current, delimiter))
  const [parent, setParent] = useState(currentParent ?? TOP_LEVEL)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const chosenParent = parent === TOP_LEVEL ? null : parent
  const problem = nameError(t, name, delimiter, chosenParent)
  // Anywhere but inside itself; its own parent even if the server did not list it.
  const parents = [
    ...new Set([
      ...(currentParent === null ? [] : [currentParent]),
      ...folders.map((each) => each.path).filter((path) => !isWithin(path, current, delimiter)),
    ]),
  ].sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }))
  const unchanged = chosenParent === currentParent && name === ownName(current, delimiter)
  const named = folderName(t, folder)

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (problem !== null || unchanged || busy) {
      return
    }
    setBusy(true)
    setFailure(null)
    try {
      onDone({ kind: 'renamed', answer: await renameFolder(accountId, current, chosenParent, name) })
    } catch (error) {
      setFailure(mailErrorMessage(t, error, t('folderActions.failed')))
      setBusy(false)
    }
  }

  return (
    <Dialog label={t('folderActions.renameTitle', { name: named })} onCancel={onCancel}>
      <form noValidate onSubmit={(event) => void submit(event)} className="flex w-80 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{t('folderActions.renameTitle', { name: named })}</h2>
        <NameField id={id} value={name} error={problem} onChange={setName} />
        {/* A server without a hierarchy has nowhere to move a folder to. */}
        {delimiter !== null && (
        <div className="flex flex-col gap-1">
          <label htmlFor={parentId} className="text-sm font-medium">
            {t('folderActions.parent')}
          </label>
          <select
            id={parentId}
            value={parent}
            onChange={(event) => {
              setParent(event.target.value)
            }}
            className="h-9 rounded-md border border-input bg-transparent px-2 text-sm"
          >
            <option value={TOP_LEVEL}>{t('folderActions.topLevel')}</option>
            {parents.map((path) => (
              <option key={path} value={path}>
                {path}
              </option>
            ))}
          </select>
        </div>
        )}
        <p className="text-sm text-muted-foreground">{t('folderActions.renameNote')}</p>
        {failure !== null && (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <Buttons
          cancel={t('folderActions.cancel')}
          confirm={t('folderActions.renameSave')}
          disabled={busy || unchanged || problem !== null}
          onCancel={onCancel}
        />
      </form>
    </Dialog>
  )
}

/** The most rule sets a `folder_in_use` refusal lists. */
const MAX_IN_USE_LISTED = 10

interface RuleSetRef {
  id: number
  name: string
}

function ruleSetsOf(error: ApiRequestError): RuleSetRef[] {
  const listed = error.field('rule_sets')
  return Array.isArray(listed)
    ? listed.filter(
        (each): each is RuleSetRef =>
          typeof each === 'object' && each !== null && typeof (each as RuleSetRef).id === 'number' && typeof (each as RuleSetRef).name === 'string',
      )
    : []
}

type DeleteState =
  | { kind: 'counting' }
  | { kind: 'countFailed'; message: string }
  | { kind: 'ready'; count: number; changed: boolean }
  | { kind: 'inUse'; ruleSets: RuleSetRef[] }
  | { kind: 'failed'; count: number; message: string }

/**
 * Deletes a folder and its mail for good, once the user confirmed its message count: counted
 * afresh when the dialog opens, and counted again by the server, which refuses a different count
 * (`folder_changed`, carrying it, for the user to confirm again).
 */
function DeleteDialog({
  accountId,
  folder,
  gmail,
  onCancel,
  onDone,
}: {
  accountId: string
  folder: MailFolder
  gmail: boolean
  onCancel: () => void
  onDone: (done: FolderDone) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [state, setState] = useState<DeleteState>({ kind: 'counting' })
  const [busy, setBusy] = useState(false)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const name = folderName(t, folder)

  useEffect(() => {
    const controller = new AbortController()
    listMailFolders(accountId, controller.signal).then(
      (listed) => {
        const fresh = listed.find((each) => isSameFolder(each.path, folder.path))
        setState(
          fresh === undefined
            ? { kind: 'countFailed', message: t('mailErrors.mailboxMissing') }
            : { kind: 'ready', count: fresh.total, changed: false },
        )
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'countFailed', message: mailErrorMessage(t, error, t('folderActions.countFailed')) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [accountId, folder.path, t])

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if ((state.kind !== 'ready' && state.kind !== 'failed') || busy) {
      return
    }
    const { count } = state
    setBusy(true)
    try {
      await deleteFolder(accountId, folder.path, count)
      onDone({ kind: 'deleted', path: folder.path })
    } catch (error) {
      setBusy(false)
      const code = error instanceof ApiRequestError ? error.code : undefined
      const now = error instanceof ApiRequestError ? error.field('messages') : undefined
      if (code === 'folder_changed' && typeof now === 'number') {
        setState({ kind: 'ready', count: now, changed: true })
        // The new count is a new question: Enter again must not answer it unread.
        cancelRef.current?.focus()
      } else if (code === 'folder_in_use' && error instanceof ApiRequestError) {
        setState({ kind: 'inUse', ruleSets: ruleSetsOf(error) })
      } else {
        setState({ kind: 'failed', count, message: mailErrorMessage(t, error, t('folderActions.failed')) })
      }
    }
  }

  const count = state.kind === 'ready' || state.kind === 'failed' ? state.count : null
  return (
    <Dialog label={t('folderActions.deleteTitle', { name })} onCancel={onCancel}>
      <form noValidate onSubmit={(event) => void submit(event)} className="flex w-96 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{t('folderActions.deleteTitle', { name })}</h2>
        {state.kind === 'counting' && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('folderActions.counting')}
          </p>
        )}
        {state.kind === 'countFailed' && (
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
        )}
        {state.kind === 'ready' && state.changed && (
          <p role="alert" className="text-sm font-medium">
            {t('folderActions.changed', { count: state.count })}
          </p>
        )}
        {count !== null && (
          <p className="text-sm">
            {count === 0 ? t('folderActions.deleteEmpty', { name }) : t('folderActions.deleteQuestion', { name, count })}
          </p>
        )}
        {count !== null && count > 0 && gmail && <p className="text-sm text-muted-foreground">{t('folderActions.gmail')}</p>}
        {state.kind === 'failed' && (
          <p role="alert" className="text-sm text-destructive">
            {state.message}
          </p>
        )}
        {state.kind === 'inUse' && (
          <div role="alert" className="flex flex-col gap-1 text-sm">
            <p>{t('folderActions.inUse')}</p>
            {state.ruleSets.length >= MAX_IN_USE_LISTED && <p>{t('folderActions.inUseFirst', { count: state.ruleSets.length })}</p>}
            <ul className="list-disc pl-5">
              {state.ruleSets.map((ruleSet) => (
                <li key={ruleSet.id}>
                  <Link to="/filters/$ruleSetId" params={{ ruleSetId: String(ruleSet.id) }} className="underline">
                    {ruleSet.name}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        <Buttons
          cancel={t('folderActions.cancel')}
          confirm={count === null ? null : t('folderActions.deleteForGood')}
          disabled={busy}
          cancelRef={cancelRef}
          destructive
          onCancel={onCancel}
        />
      </form>
    </Dialog>
  )
}
