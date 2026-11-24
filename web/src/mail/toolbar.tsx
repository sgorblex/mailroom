import { createElement, useEffect, useRef, useState, type ReactElement, type ReactNode, type Ref, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { X } from 'lucide-react'

import type { AccountId } from '@/api/accounts.ts'
import { useAccountName } from '@/accounts/accounts-context.ts'
import type { MailFolder, MoveRemoval } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { AccountMovePicker, MoveForGoodDialog } from './account-move-picker.tsx'
import { listedActions, triageShortcut, unavailableReason, type ActionContext } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { MAIL_NS } from './i18n.ts'
import { useUGoesBack } from './reading-pane.ts'
import { folderName, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { useTriageActions, type TriageActions } from './triage-actions.ts'
import { failureKey, isSameFolder, loadedFolders, useTriage, type Action, type Destination, type Outcome } from './triage-context.ts'

/** `not_found` of a batch: messages that left the folder since the list was read. */
const GONE = 'triage.gone'
/** The live regions' ids, which the notice's Dismiss button is described by (one mail screen at a time). */
const STATUS_ID = 'triage-outcome-status'
const ALERT_ID = 'triage-outcome-alert'

/**
 * The actions on the selection (the open message has its own, `MessageActions`), with what the
 * last action did when it did not go through in full (shown here, announced by
 * `OutcomeAnnouncer`; one that did is the toast's). Each button names its keyboard shortcut.
 */
export function Toolbar({ className }: { className?: string }): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const triage = useTriage()
  const actions = useTriageActions()
  const compose = useCompose()
  const trigger = useRef<HTMLButtonElement>(null)
  const accountTrigger = useRef<HTMLButtonElement>(null)
  const selecting = triage.selection.size > 0 && actions.targets.length > 0
  const picker = selecting && triage.picker
  const accountPicker = selecting && triage.accountPicker

  if (!selecting && triage.outcome === null) {
    return null
  }
  const context: ActionContext = {
    actions,
    message: null,
    account: null,
    compose,
    // The toolbar's buttons open and close their pickers.
    openPicker: (kind) => {
      if (kind === 'move') {
        triage.setPicker(!picker)
      } else {
        triage.setAccountPicker(!accountPicker)
      }
    },
  }
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {selecting && (
        <div
          role="group"
          aria-label={t('triage.selectionLabel')}
          aria-busy={triage.busy}
          className="flex flex-wrap items-center gap-1"
        >
          <span className="mr-auto text-sm text-muted-foreground">
            {actions.matching !== null
              ? t('matching.selected', { count: actions.matching })
              : t('triage.selected', { count: triage.selection.size })}
          </span>
          <TriageButtons
            context={context}
            busy={triage.busy}
            shortcuts
            picker={picker}
            accountPicker={accountPicker}
            trigger={trigger}
            accountTrigger={accountTrigger}
          />
        </div>
      )}
      {selecting && (
        <TriagePickers
          actions={actions}
          picker={picker}
          accountPicker={accountPicker}
          into={triage.accountPickerInto}
          onClosePicker={() => {
            triage.setPicker(false)
          }}
          onCloseAccountPicker={() => {
            triage.setAccountPicker(false)
          }}
          trigger={trigger}
          accountTrigger={accountTrigger}
        />
      )}
      {triage.outcome !== null && <OutcomeNotice outcome={triage.outcome} onDismiss={triage.dismissOutcome} />}
    </div>
  )
}

/**
 * The registry's triage actions listed for `context`, as icon buttons naming their shortcuts where
 * `shortcuts` (the keys act on these buttons' targets); a picker's button says whether it is open.
 * Each button carries its action's id (`data-action`); those in `overflow` are hidden, kept in
 * place for their width and their focus.
 */
export function TriageButtons({
  context,
  busy,
  shortcuts,
  picker,
  accountPicker,
  trigger,
  accountTrigger,
  overflow,
}: {
  context: ActionContext
  busy: boolean
  shortcuts: boolean
  picker: boolean
  accountPicker: boolean
  trigger: Ref<HTMLButtonElement>
  accountTrigger: Ref<HTMLButtonElement>
  overflow?: ReadonlySet<string>
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const uGoesBack = useUGoesBack()
  return (
    <>
      {listedActions('triage', context).map((action) => {
        const reason = unavailableReason(action, context)
        return (
          <ToolButton
            key={action.id}
            ref={action.picker === 'move' ? trigger : action.picker === 'account' ? accountTrigger : undefined}
            action={action.id}
            hidden={overflow?.has(action.id) === true}
            label={t(action.label(context))}
            shortcut={triageShortcut(action, shortcuts, uGoesBack)}
            disabled={busy}
            unavailable={reason === null ? undefined : t(reason)}
            expanded={action.picker === 'move' ? picker : action.picker === 'account' ? accountPicker : undefined}
            onClick={() => {
              action.run(context)
            }}
          >
            {createElement(action.icon(context), { 'aria-hidden': true })}
          </ToolButton>
        )
      })}
    </>
  )
}

/**
 * The move picker and the account move picker on `actions`' targets, while open (the account one
 * on `into` where a drop opened it), and the confirmation of a move that deletes the originals for
 * good. Closing a picker puts focus back on its button, unless focus went somewhere else.
 */
export function TriagePickers({
  actions,
  picker,
  accountPicker,
  into,
  onClosePicker,
  onCloseAccountPicker,
  trigger,
  accountTrigger,
}: {
  actions: TriageActions
  picker: boolean
  accountPicker: boolean
  into: Destination | null
  onClosePicker: () => void
  onCloseAccountPicker: () => void
  trigger: RefObject<HTMLButtonElement | null>
  accountTrigger: RefObject<HTMLButtonElement | null>
}): ReactElement {
  const showPicker = picker && actions.moveChoices !== null
  const showAccountPicker = accountPicker && actions.otherAccounts !== null
  const pickerWasOpen = useRef(false)
  const accountPickerWasOpen = useRef(false)
  // A move to another account that deletes the originals for good, waiting for the user's yes.
  const [forGood, setForGood] = useState<{ account: AccountId; folder: MailFolder } | null>(null)

  useEffect(() => {
    if (pickerWasOpen.current && !showPicker && (document.activeElement === null || document.activeElement === document.body)) {
      trigger.current?.focus()
    }
    pickerWasOpen.current = showPicker
  }, [showPicker, trigger])
  useEffect(() => {
    if (
      accountPickerWasOpen.current &&
      !showAccountPicker &&
      forGood === null &&
      (document.activeElement === null || document.activeElement === document.body)
    ) {
      accountTrigger.current?.focus()
    }
    accountPickerWasOpen.current = showAccountPicker
  }, [showAccountPicker, forGood, accountTrigger])

  return (
    <>
      {showPicker && actions.moveChoices !== null && (
        <MovePicker
          folders={actions.moveChoices}
          onChoose={(folder) => {
            onClosePicker()
            actions.move(folder)
          }}
        />
      )}
      {showAccountPicker && actions.otherAccounts !== null && (
        <AccountMovePicker
          // A drop on another folder opens it anew there.
          key={into === null ? '' : `${String(into.account)} ${into.folder.path}`}
          accounts={actions.otherAccounts}
          into={into}
          onChoose={(account: AccountId, folder: MailFolder, removal: MoveRemoval) => {
            onCloseAccountPicker()
            if (removal === 'delete') {
              setForGood({ account, folder })
            } else {
              actions.moveToAccount(account, folder, removal)
            }
          }}
        />
      )}
      {forGood !== null && (
        <MoveForGoodDialog
          count={actions.targets.length}
          from={[...new Set(actions.targets.map((target) => target.account_id))]}
          account={forGood.account}
          folder={forGood.folder}
          onCancel={() => {
            setForGood(null)
          }}
          onConfirm={() => {
            setForGood(null)
            actions.moveToAccount(forGood.account, forGood.folder, 'delete')
          }}
        />
      )}
    </>
  )
}

/** A toolbar action; `unavailable`, the reason it cannot be done here, greys it out and says why. */
function ToolButton({
  action,
  hidden,
  label,
  shortcut,
  disabled,
  unavailable,
  expanded,
  onClick,
  ref,
  children,
}: {
  action: string
  hidden: boolean
  label: string
  shortcut: string | undefined
  disabled: boolean
  unavailable?: string
  expanded?: boolean
  onClick: () => void
  ref?: Ref<HTMLButtonElement>
  children: ReactNode
}): ReactElement {
  const named = shortcut === undefined ? label : `${label} (${shortcut})`
  return (
    <Button
      ref={ref}
      data-action={action}
      hidden={hidden}
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={unavailable === undefined ? named : `${named}: ${unavailable}`}
      aria-keyshortcuts={unavailable === undefined ? shortcut : undefined}
      aria-disabled={disabled || unavailable !== undefined}
      className={cn(unavailable !== undefined && 'opacity-50')}
      aria-haspopup={expanded === undefined ? undefined : 'dialog'}
      aria-expanded={expanded}
      onClick={() => {
        if (!disabled && unavailable === undefined) {
          onClick()
        }
      }}
    >
      {children}
    </Button>
  )
}

function MovePicker({
  folders,
  onChoose,
}: {
  folders: MailFolder[]
  onChoose: (folder: MailFolder) => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const first = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    first.current?.focus()
  }, [])
  return (
    <div role="dialog" aria-label={t('triage.moveTo')} className="flex flex-col gap-1 rounded-lg border p-2">
      <p className="px-2 text-xs text-muted-foreground">{t('triage.moveTo')}</p>
      {folders.length === 0 ? (
        <p className="px-2 text-sm text-muted-foreground">{t('triage.noFolders')}</p>
      ) : (
        <ul className="flex max-h-64 flex-col overflow-y-auto">
          {folders.map((folder, index) => (
            <li key={folder.path}>
              <Button
                ref={index === 0 ? first : undefined}
                variant="ghost"
                size="sm"
                title={folder.path}
                className="w-full justify-start"
                onClick={() => {
                  onChoose(folder)
                }}
              >
                <span className="truncate">{folderName(t, folder)}</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function doneMessage(t: TFunction, accountName: (id: AccountId) => string, action: Action, count: number): string {
  switch (action.kind) {
    case 'archive':
      return t('triage.archived', { count })
    case 'junk':
      return t('triage.reported', { count })
    case 'trash':
      return t('triage.trashed', { count })
    case 'move':
      return t('triage.moved', { count, folder: folderName(t, action.folder) })
    case 'moveToAccount':
      return t(action.removal === 'delete' ? 'triage.movedToAccountForGood' : 'triage.movedToAccount', {
        count,
        account: accountName(action.account),
        folder: folderName(t, action.folder),
      })
    case 'flag':
      if (action.change.flagged !== undefined) {
        return action.change.flagged ? t('triage.starred', { count }) : t('triage.unstarred', { count })
      }
      if (action.change.to_sort !== undefined) {
        return action.change.to_sort ? t('triage.markedToSort', { count }) : t('triage.markedSorted', { count })
      }
      return action.change.seen === true ? t('triage.markedRead', { count }) : t('triage.markedUnread', { count })
  }
}

/** What an action that went through in full did, in words: the toast's message. */
export function DoneMessage({ action, count }: { action: Action; count: number }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accountName = useAccountName()
  return <>{doneMessage(t, accountName, action, count)}</>
}

function failedOutcome(outcome: Outcome): boolean {
  return outcome.kind === 'refused' || outcome.failures.length > 0
}

/** What the last action did, in words: for the notice and for the live regions alike. */
function OutcomeText({ outcome }: { outcome: Outcome }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { folders } = useTriage()
  const accountName = useAccountName()
  // A failed group's folder as the sidebar names it, once its account's folders are known.
  const nameOf = (account: AccountId, path: string): string => {
    const folder = loadedFolders(folders[account])?.find((each) => isSameFolder(each.path, path))
    return folder === undefined ? path : folderName(t, folder)
  }
  if (outcome.kind === 'refused') {
    return <p>{mailErrorMessage(t, outcome.error, t('triage.failed'), GONE)}</p>
  }
  return (
    <>
      {outcome.count > 0 && <p>{doneMessage(t, accountName, outcome.action, outcome.count)}</p>}
      {outcome.failures.length > 0 && (
        <>
          <p className="font-medium">{t('triage.partial')}</p>
          <ul className="flex flex-col gap-0.5">
            {outcome.failures.map((failure) => (
              <li key={failureKey(failure)}>
                {t('list.failedFolder', {
                  account: accountName(failure.account),
                  folder: nameOf(failure.account, failure.folder),
                  reason:
                    failure.inBoth !== undefined && outcome.action.kind === 'moveToAccount'
                      ? t('triage.inBoth', { count: failure.inBoth, account: accountName(outcome.action.account) })
                      : mailCodeMessage(t, failure.code, GONE),
                })}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}

/** The notice as seen; screen readers hear it from `OutcomeAnnouncer` instead. */
function OutcomeNotice({ outcome, onDismiss }: { outcome: Outcome; onDismiss: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-lg border p-2 text-sm',
        failedOutcome(outcome) && 'border-destructive/40',
      )}
    >
      <div aria-hidden="true" className="flex min-w-0 flex-1 flex-col gap-1">
        <OutcomeText outcome={outcome} />
      </div>
      {/* The text above is hidden from screen readers: the live region holding it describes the button. */}
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={t('triage.dismiss')}
        aria-describedby={failedOutcome(outcome) ? ALERT_ID : STATUS_ID}
        onClick={onDismiss}
      >
        <X aria-hidden="true" />
      </Button>
    </div>
  )
}

/**
 * The last action's outcome for screen readers: two live regions mounted for as long as the mail
 * screen is, whose text alone changes (a region inserted together with its text is often not
 * announced). A failure goes to the assertive one.
 */
export function OutcomeAnnouncer(): ReactElement {
  const { outcome } = useTriage()
  const failed = outcome !== null && failedOutcome(outcome)
  return (
    <>
      <div id={STATUS_ID} role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {outcome !== null && !failed && <OutcomeText outcome={outcome} />}
      </div>
      <div id={ALERT_ID} role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {outcome !== null && failed && <OutcomeText outcome={outcome} />}
      </div>
    </>
  )
}
