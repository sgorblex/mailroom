import { useEffect, useId, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import type { AccountId } from '@/api/accounts.ts'
import { AccountName } from '@/accounts/account-name.tsx'
import { useAccountLabel, useAccountName } from '@/accounts/accounts-context.ts'
import type { MailFolder, MoveRemoval } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { cn } from '@/lib/utils.ts'

import { Buttons } from './folder-dialogs.tsx'
import { MAIL_NS } from './i18n.ts'
import { folderName, mailErrorMessage } from './messages.ts'
import { movableInto } from './triage-actions.ts'
import { isSameFolder, useAccountFolders, type Destination } from './triage-context.ts'

/**
 * Moving to another of the user's accounts (their ids): the account (skipped when there is one),
 * then what becomes of the originals and the folder, which `onChoose` takes. Inline, as the
 * folder picker. `into`, where a drop chose to move to, opens it on that account's folders with
 * focus on that folder, the originals going to Trash.
 */
export function AccountMovePicker({
  accounts,
  into = null,
  onChoose,
}: {
  accounts: AccountId[]
  into?: Destination | null
  onChoose: (account: AccountId, folder: MailFolder, removal: MoveRemoval) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [only] = accounts
  const [account, setAccount] = useState<AccountId | null>(
    into !== null && accounts.includes(into.account) ? into.account : accounts.length === 1 && only !== undefined ? only : null,
  )
  return (
    <div role="dialog" aria-label={t('triage.moveToAccountTitle')} className="flex flex-col gap-1 rounded-lg border p-2">
      {account === null ? (
        <AccountStep accounts={accounts} onChoose={setAccount} />
      ) : (
        <FolderStep
          account={account}
          preset={into?.account === account ? into.folder : null}
          onBack={
            accounts.length > 1
              ? () => {
                  setAccount(null)
                }
              : null
          }
          onChoose={(folder, removal) => {
            onChoose(account, folder, removal)
          }}
        />
      )}
    </div>
  )
}

function AccountStep({ accounts, onChoose }: { accounts: AccountId[]; onChoose: (account: AccountId) => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accountLabel = useAccountLabel()
  const first = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    first.current?.focus()
  }, [])
  return (
    <>
      <p className="px-2 text-xs text-muted-foreground">{t('triage.moveToAccountTitle')}</p>
      <ul className="flex flex-col">
        {accounts.map((account, index) => (
          <li key={account}>
            <Button
              ref={index === 0 ? first : undefined}
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => {
                onChoose(account)
              }}
            >
              <AccountName account={accountLabel(account)} />
            </Button>
          </li>
        ))}
      </ul>
    </>
  )
}

function FolderStep({
  account,
  preset,
  onBack,
  onChoose,
}: {
  account: AccountId
  /** The folder focused first, instead of the heading, once listed. */
  preset: MailFolder | null
  onBack: (() => void) | null
  onChoose: (folder: MailFolder, removal: MoveRemoval) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accountName = useAccountName()
  const { state, retry } = useAccountFolders(account)
  const [removal, setRemoval] = useState<MoveRemoval>('trash')
  const name = useId()
  const heading = useRef<HTMLParagraphElement>(null)
  const presetPath = preset?.path ?? null
  const movable = state.kind === 'loaded' ? state.value.filter(movableInto) : null
  // The heading has focus unless the preset folder is listed: while loading, on a failure, or when
  // it is gone since the sidebar was read.
  const presetListed = presetPath !== null && movable?.some((folder) => isSameFolder(folder.path, presetPath)) === true
  useEffect(() => {
    if (!presetListed) {
      heading.current?.focus()
    }
  }, [presetListed])
  return (
    <>
      <div className="flex items-center gap-2 px-2">
        <p ref={heading} tabIndex={-1} className="mr-auto text-xs text-muted-foreground outline-none">
          {t('triage.moveToAccountFolder', { account: accountName(account) })}
        </p>
        {onBack !== null && (
          <Button variant="ghost" size="xs" onClick={onBack}>
            {t('triage.otherAccount')}
          </Button>
        )}
      </div>
      <fieldset className="flex flex-col gap-1 px-2 py-1 text-sm">
        <legend className="text-xs text-muted-foreground">{t('triage.originals')}</legend>
        {(['trash', 'delete'] as const).map((choice) => (
          <label key={choice} className="flex items-center gap-2">
            <input
              type="radio"
              name={name}
              value={choice}
              checked={removal === choice}
              onChange={() => {
                setRemoval(choice)
              }}
            />
            {choice === 'trash' ? t('triage.originalsToTrash') : t('triage.originalsForGood')}
          </label>
        ))}
      </fieldset>
      {state.kind === 'loading' && (
        <p role="status" className="px-2 text-sm text-muted-foreground">
          {t('triage.loadingFolders')}
        </p>
      )}
      {state.kind === 'failed' && (
        <div className="flex items-center gap-2 px-2">
          <p role="alert" className="text-sm text-destructive">
            {mailErrorMessage(t, state.error, t('triage.foldersFailed'))}
          </p>
          <Button variant="outline" size="xs" onClick={retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {movable !== null && (
        <FolderList
          folders={movable}
          preset={presetListed ? presetPath : null}
          onChoose={(folder) => {
            onChoose(folder, removal)
          }}
        />
      )}
    </>
  )
}

function FolderList({
  folders,
  preset,
  onChoose,
}: {
  folders: MailFolder[]
  preset: string | null
  onChoose: (folder: MailFolder) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const presetButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    presetButton.current?.focus()
  }, [])
  if (folders.length === 0) {
    return <p className="px-2 text-sm text-muted-foreground">{t('triage.noFolders')}</p>
  }
  return (
    <ul className="flex max-h-64 flex-col overflow-y-auto">
      {folders.map((folder) => {
        const isPreset = preset !== null && isSameFolder(folder.path, preset)
        return (
          <li key={folder.path}>
            <Button
              ref={isPreset ? presetButton : undefined}
              variant="ghost"
              size="sm"
              title={folder.path}
              className={cn('w-full justify-start', isPreset && 'bg-accent')}
              onClick={() => {
                onChoose(folder)
              }}
            >
              <span className="truncate">{folderName(t, folder)}</span>
            </Button>
          </li>
        )
      })}
    </ul>
  )
}

/** The question before a move to another account deletes the originals for good. */
export function MoveForGoodDialog({
  count,
  from,
  account,
  folder,
  onCancel,
  onConfirm,
}: {
  count: number
  from: AccountId[]
  account: AccountId
  folder: MailFolder
  onCancel: () => void
  onConfirm: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accountName = useAccountName()
  const title = t('triage.forGoodTitle')
  function submit(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    onConfirm()
  }
  return (
    <Dialog label={title} onCancel={onCancel}>
      <form noValidate onSubmit={submit} className="flex w-96 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        <p className="text-sm">
          {t('triage.forGoodQuestion', {
            count,
            from: from.map(accountName).join(', '),
            account: accountName(account),
            folder: folderName(t, folder),
          })}
        </p>
        <Buttons
          cancel={t('triage.cancel')}
          confirm={t('triage.forGoodConfirm', { count })}
          disabled={false}
          destructive
          onCancel={onCancel}
        />
      </form>
    </Dialog>
  )
}
