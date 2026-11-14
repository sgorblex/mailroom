import { useEffect, useRef, useState, type ReactElement, type ReactNode, type Ref } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Archive, ArrowRightLeft, FolderInput, Mail, MailOpen, Star, StarOff, Trash2, X } from 'lucide-react'

import type { MailFolder, MoveRemoval } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { AccountMovePicker, MoveForGoodDialog } from './account-move-picker.tsx'
import { MAIL_NS } from './i18n.ts'
import { folderName, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { useTriageActions } from './triage-actions.ts'
import { failureKey, isSameFolder, loadedFolders, useTriage, type Action, type Outcome } from './triage-context.ts'

/** `not_found` of a batch: messages that left the folder since the list was read. */
const GONE = 'triage.gone'
/** The live regions' ids, which the notice's Dismiss button is described by (one mail screen at a time). */
const STATUS_ID = 'triage-outcome-status'
const ALERT_ID = 'triage-outcome-alert'

/**
 * The actions on the selection, or on the open message when nothing is selected, with what the
 * last one did (shown here, announced by `OutcomeAnnouncer`). Each button names its keyboard
 * shortcut.
 */
export function Toolbar({ className }: { className?: string }): ReactElement | null {
  const { t } = useTranslation(MAIL_NS)
  const triage = useTriage()
  const actions = useTriageActions()
  const trigger = useRef<HTMLButtonElement>(null)
  const pickerWasOpen = useRef(false)
  const picker = triage.picker && actions.moveChoices !== null
  const accountTrigger = useRef<HTMLButtonElement>(null)
  const accountPickerWasOpen = useRef(false)
  const showAccountPicker = triage.accountPicker && actions.otherAccounts !== null
  // A move to another account that deletes the originals for good, waiting for the user's yes.
  const [forGood, setForGood] = useState<{ account: string; folder: MailFolder } | null>(null)

  // Closing a picker puts focus back on its button, unless focus went somewhere else.
  useEffect(() => {
    if (pickerWasOpen.current && !picker && (document.activeElement === null || document.activeElement === document.body)) {
      trigger.current?.focus()
    }
    pickerWasOpen.current = picker
  }, [picker])
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
  }, [showAccountPicker, forGood])

  if (actions.targets.length === 0 && triage.outcome === null) {
    return null
  }
  const disabled = triage.busy
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {actions.targets.length > 0 && (
        <div
          role="group"
          aria-label={t('triage.label')}
          aria-busy={disabled}
          className="flex flex-wrap items-center gap-1"
        >
          <span className="mr-auto text-sm text-muted-foreground">
            {triage.selection.size > 0 ? t('triage.selected', { count: triage.selection.size }) : t('triage.thisMessage')}
          </span>
          {actions.archive !== null && (
            <ToolButton label={t('triage.archive')} shortcut="e" disabled={disabled} onClick={actions.archive}>
              <Archive aria-hidden="true" />
            </ToolButton>
          )}
          {actions.trash !== null && (
            <ToolButton label={t('triage.trash')} shortcut="#" disabled={disabled} onClick={actions.trash}>
              <Trash2 aria-hidden="true" />
            </ToolButton>
          )}
          {actions.moveChoices !== null && (
            <ToolButton
              ref={trigger}
              label={t('triage.move')}
              shortcut="v"
              disabled={disabled}
              expanded={picker}
              onClick={() => {
                triage.setPicker(!picker)
              }}
            >
              <FolderInput aria-hidden="true" />
            </ToolButton>
          )}
          {actions.otherAccounts !== null && (
            <ToolButton
              ref={accountTrigger}
              label={t('triage.moveToAccount')}
              shortcut={undefined}
              disabled={disabled}
              expanded={showAccountPicker}
              onClick={() => {
                triage.setAccountPicker(!showAccountPicker)
              }}
            >
              <ArrowRightLeft aria-hidden="true" />
            </ToolButton>
          )}
          {actions.star !== null && (
            <ToolButton
              label={actions.star.flagged ? t('triage.star') : t('triage.unstar')}
              shortcut="s"
              disabled={disabled}
              onClick={actions.star.run}
            >
              {actions.star.flagged ? <Star aria-hidden="true" /> : <StarOff aria-hidden="true" />}
            </ToolButton>
          )}
          {actions.read !== null && (
            <ToolButton
              label={actions.read.seen ? t('triage.markRead') : t('triage.markUnread')}
              shortcut={actions.read.seen ? undefined : 'u'}
              disabled={disabled}
              onClick={actions.read.run}
            >
              {actions.read.seen ? <MailOpen aria-hidden="true" /> : <Mail aria-hidden="true" />}
            </ToolButton>
          )}
        </div>
      )}
      {picker && actions.moveChoices !== null && (
        <MovePicker
          folders={actions.moveChoices}
          onChoose={(folder) => {
            triage.setPicker(false)
            actions.move(folder)
          }}
        />
      )}
      {showAccountPicker && actions.otherAccounts !== null && (
        <AccountMovePicker
          accounts={actions.otherAccounts}
          onChoose={(account: string, folder: MailFolder, removal: MoveRemoval) => {
            triage.setAccountPicker(false)
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
      {triage.outcome !== null && <OutcomeNotice outcome={triage.outcome} onDismiss={triage.dismissOutcome} />}
    </div>
  )
}

function ToolButton({
  label,
  shortcut,
  disabled,
  expanded,
  onClick,
  ref,
  children,
}: {
  label: string
  shortcut: string | undefined
  disabled: boolean
  expanded?: boolean
  onClick: () => void
  ref?: Ref<HTMLButtonElement>
  children: ReactNode
}): ReactElement {
  return (
    <Button
      ref={ref}
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={shortcut === undefined ? label : `${label} (${shortcut})`}
      aria-keyshortcuts={shortcut}
      aria-disabled={disabled}
      aria-haspopup={expanded === undefined ? undefined : 'dialog'}
      aria-expanded={expanded}
      onClick={() => {
        if (!disabled) {
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

function doneMessage(t: TFunction, action: Action, count: number): string {
  switch (action.kind) {
    case 'archive':
      return t('triage.archived', { count })
    case 'trash':
      return t('triage.trashed', { count })
    case 'move':
      return t('triage.moved', { count, folder: folderName(t, action.folder) })
    case 'moveToAccount':
      return t(action.removal === 'delete' ? 'triage.movedToAccountForGood' : 'triage.movedToAccount', {
        count,
        account: action.account,
        folder: folderName(t, action.folder),
      })
    case 'flag':
      if (action.change.flagged !== undefined) {
        return action.change.flagged ? t('triage.starred', { count }) : t('triage.unstarred', { count })
      }
      return action.change.seen === true ? t('triage.markedRead', { count }) : t('triage.markedUnread', { count })
  }
}

function failedOutcome(outcome: Outcome): boolean {
  return outcome.kind === 'refused' || outcome.failures.length > 0
}

/** What the last action did, in words: for the notice and for the live regions alike. */
function OutcomeText({ outcome }: { outcome: Outcome }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { folders } = useTriage()
  // A failed group's folder as the sidebar names it, once its account's folders are known.
  const nameOf = (account: string, path: string): string => {
    const folder = loadedFolders(folders[account])?.find((each) => isSameFolder(each.path, path))
    return folder === undefined ? path : folderName(t, folder)
  }
  if (outcome.kind === 'refused') {
    return <p>{mailErrorMessage(t, outcome.error, t('triage.failed'), GONE)}</p>
  }
  return (
    <>
      {outcome.count > 0 && <p>{doneMessage(t, outcome.action, outcome.count)}</p>}
      {outcome.failures.length > 0 && (
        <>
          <p className="font-medium">{t('triage.partial')}</p>
          <ul className="flex flex-col gap-0.5">
            {outcome.failures.map((failure) => (
              <li key={failureKey(failure)}>
                {t('list.failedFolder', {
                  account: failure.account,
                  folder: nameOf(failure.account, failure.folder),
                  reason:
                    failure.inBoth !== undefined && outcome.action.kind === 'moveToAccount'
                      ? t('triage.inBoth', { count: failure.inBoth, account: outcome.action.account })
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
