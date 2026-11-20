import {
  Archive,
  ArrowRightLeft,
  BookmarkCheck,
  BookmarkPlus,
  FileDown,
  FolderInput,
  Forward,
  ListFilter,
  Mail,
  MailOpen,
  PenLine,
  Printer,
  Reply,
  ReplyAll,
  Star,
  StarOff,
  Trash2,
  type LucideIcon,
} from 'lucide-react'

import type { AccountId } from '@/api/accounts.ts'
import { messagePrintUrl, messageSourceUrl, type PrefillMode } from '@/api/mail.ts'

import type { Compose } from './compose-context.ts'
import { movableInto, type TriageActions } from './triage-actions.ts'
import { isSameFolder, type Destination, type Target } from './triage-context.ts'

/** What an action is run on, and what it runs with. */
export interface ActionContext {
  /** The triage actions on the targets. */
  actions: TriageActions
  /** The message a reply or a forward answers: the open one, or the one a menu is for. */
  message: Pick<Target, 'ref' | 'account_id'> | null
  /** The account a new message is written on, where one can be. */
  account: AccountId | null
  compose: Pick<Compose, 'start' | 'canCompose'>
  /** Opens (or, from the toolbar, toggles) the move picker or the account move picker, on `into` where a drop chose it. */
  openPicker: (picker: 'move' | 'account', into?: Destination) => void
  /** Where a drag dropped the targets: a move goes there instead of opening a picker. */
  into?: Destination
  /** Opens a filter drafted from the message, where the instance runs filters. */
  draftFilter?: (message: Pick<Target, 'ref' | 'account_id'>) => void
}

/**
 * A mail action: the toolbar, the shortcuts, the reply buttons and the context menus all read
 * these. `triage` ones act on the targets and wait while another runs; `reply` and `compose` ones
 * open the composer, and their keys wait while one is open; `message` ones open the message in
 * another form, in a tab or as a download, or a filter drafted from it, and never wait.
 */
export interface MailAction {
  id: string
  group: 'reply' | 'compose' | 'triage' | 'message'
  /** Its name's key in the `mail` namespace. */
  label: (context: ActionContext) => string
  icon: (context: ActionContext) => LucideIcon
  shortcut?: string
  /** Its name in the shortcut sheet, where `label` depends on the targets. */
  sheetLabel?: string
  /** Whether it can be run on these targets. */
  available: (context: ActionContext) => boolean
  /** Whether the toolbar and the menus list it, when available; the key works either way. */
  listed?: (context: ActionContext) => boolean
  /** Why it is offered greyed out and not run (a key in the `mail` namespace), or `null`. */
  unavailable?: (context: ActionContext) => string | null
  run: (context: ActionContext) => void
  /** Opens a picker instead of acting at once. */
  picker?: 'move' | 'account'
  destructive?: boolean
  /** It acts on every message the list or search matches when they are selected; the other triage actions are greyed out then. */
  matching?: boolean
}

function answer(id: string, mode: PrefillMode, label: string, icon: LucideIcon, shortcut: string): MailAction {
  return {
    id,
    group: 'reply',
    label: () => label,
    icon: () => icon,
    shortcut,
    available: ({ message }) => message !== null,
    unavailable: ({ message, compose }) =>
      message !== null && !compose.canCompose(message.account_id) ? 'compose.offHint' : null,
    run: ({ message, compose }) => {
      if (message !== null) {
        compose.start({ kind: mode, account: message.account_id, ref: message.ref })
      }
    },
  }
}

/** Opens `url` in a new tab, or saves it when `download` (the API names the file). */
function openUrl(url: string, download: boolean): void {
  const link = document.createElement('a')
  link.href = url
  if (download) {
    link.download = ''
  } else {
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  }
  link.click()
}

function messageForm(id: string, label: string, icon: LucideIcon, url: (ref: string) => string, download: boolean): MailAction {
  return {
    id,
    group: 'message',
    label: () => label,
    icon: () => icon,
    available: ({ message }) => message !== null,
    run: ({ message }) => {
      if (message !== null) {
        openUrl(url(message.ref), download)
      }
    },
  }
}

export const MAIL_ACTIONS: readonly MailAction[] = [
  answer('reply', 'reply', 'compose.reply', Reply, 'r'),
  answer('replyAll', 'reply_all', 'compose.replyAll', ReplyAll, 'a'),
  answer('forward', 'forward', 'compose.forward', Forward, 'f'),
  messageForm('print', 'message.print', Printer, messagePrintUrl, false),
  messageForm('downloadSource', 'message.downloadSource', FileDown, messageSourceUrl, true),
  {
    id: 'filterLike',
    group: 'message',
    label: () => 'message.filterLike',
    icon: () => ListFilter,
    available: ({ message, draftFilter }) => message !== null && draftFilter !== undefined,
    run: ({ message, draftFilter }: ActionContext): void => {
      if (message !== null) {
        draftFilter?.(message)
      }
    },
  },
  {
    id: 'compose',
    group: 'compose',
    label: () => 'compose.compose',
    icon: () => PenLine,
    shortcut: 'c',
    available: ({ account }) => account !== null,
    unavailable: ({ account, compose }) => (account === null || compose.canCompose(account) ? null : 'compose.offHint'),
    run: ({ account, compose }: ActionContext): void => {
      if (account !== null) {
        compose.start({ kind: 'new', account })
      }
    },
  },
  {
    id: 'archive',
    group: 'triage',
    label: () => 'triage.archive',
    icon: () => Archive,
    shortcut: 'e',
    available: ({ actions }) => actions.archive !== null,
    run: ({ actions }) => actions.archive?.(),
  },
  {
    id: 'trash',
    group: 'triage',
    label: () => 'triage.trash',
    icon: () => Trash2,
    shortcut: '#',
    available: ({ actions }) => actions.trash !== null,
    run: ({ actions }) => actions.trash?.(),
    destructive: true,
    matching: true,
  },
  {
    id: 'move',
    group: 'triage',
    label: () => 'triage.move',
    icon: () => FolderInput,
    shortcut: 'v',
    available: ({ actions }) => actions.moveChoices !== null,
    run: ({ actions, openPicker, into }: ActionContext): void => {
      if (into === undefined) {
        openPicker('move')
      } else {
        actions.move(into.folder)
      }
    },
    picker: 'move',
    matching: true,
  },
  {
    id: 'moveToAccount',
    group: 'triage',
    label: () => 'triage.moveToAccount',
    icon: () => ArrowRightLeft,
    available: ({ actions }) => actions.otherAccounts !== null,
    run: ({ openPicker, into }: ActionContext): void => {
      openPicker('account', into)
    },
    picker: 'account',
  },
  {
    id: 'star',
    group: 'triage',
    label: ({ actions }) => (actions.star?.flagged === false ? 'triage.unstar' : 'triage.star'),
    icon: ({ actions }) => (actions.star?.flagged === false ? StarOff : Star),
    shortcut: 's',
    sheetLabel: 'shortcuts.star',
    available: ({ actions }) => actions.star !== null,
    run: ({ actions }) => actions.star?.run(),
    matching: true,
  },
  {
    // Every matching message selected: star sets, this clears.
    id: 'unstar',
    group: 'triage',
    label: () => 'triage.unstar',
    icon: () => StarOff,
    available: ({ actions }) => actions.unstar !== null,
    run: ({ actions }) => actions.unstar?.(),
    matching: true,
  },
  {
    id: 'toSort',
    group: 'triage',
    label: ({ actions }) => (actions.toSort?.toSort === false ? 'triage.sorted' : 'triage.toSort'),
    icon: ({ actions }) => (actions.toSort?.toSort === false ? BookmarkCheck : BookmarkPlus),
    shortcut: 't',
    sheetLabel: 'shortcuts.toSort',
    available: ({ actions }) => actions.toSort !== null,
    unavailable: ({ actions }) => (actions.toSort?.unsupported === true ? 'triage.toSortUnsupported' : null),
    run: ({ actions }) => actions.toSort?.run(),
    matching: true,
  },
  {
    // Every matching message selected: To sort sets, this clears.
    id: 'markSorted',
    group: 'triage',
    label: () => 'triage.sorted',
    icon: () => BookmarkCheck,
    available: ({ actions }) => actions.markSorted !== null,
    unavailable: ({ actions }) => (actions.toSort?.unsupported === true ? 'triage.toSortUnsupported' : null),
    run: ({ actions }) => actions.markSorted?.(),
    matching: true,
  },
  {
    id: 'markRead',
    group: 'triage',
    label: () => 'triage.markRead',
    icon: () => MailOpen,
    available: ({ actions }) => actions.read?.seen === true,
    run: ({ actions }) => actions.read?.run(),
    matching: true,
  },
  {
    // Listed when every target is read, or every matching message is selected; `u` marks unread whatever they are.
    id: 'markUnread',
    group: 'triage',
    label: () => 'triage.markUnread',
    icon: () => Mail,
    shortcut: 'u',
    available: ({ actions }) => actions.markUnread !== null,
    listed: ({ actions }) => actions.read?.seen === false || actions.matching !== null,
    run: ({ actions }) => actions.markUnread?.(),
    matching: true,
  },
]

/** The actions of `group` the toolbar or a menu lists for this context, in order. */
export function listedActions(group: MailAction['group'], context: ActionContext): MailAction[] {
  return MAIL_ACTIONS.filter(
    (action) => action.group === group && action.available(context) && (action.listed?.(context) ?? true),
  )
}

/** Why `action` is greyed out here, or `null` when it can run. */
export function unavailableReason(action: MailAction, context: ActionContext): string | null {
  if (context.actions.matching !== null && action.group === 'triage' && action.matching !== true) {
    return 'matching.unavailable'
  }
  return action.unavailable?.(context) ?? null
}

/** Whether `action` can run now: available, not greyed out, not waiting on `busy` (triage) or an open composer (reply and compose). */
export function runnable(
  action: MailAction,
  context: ActionContext,
  { busy, composing }: { busy: boolean; composing: boolean },
): boolean {
  const waiting = action.group === 'triage' ? busy : action.group !== 'message' && composing
  return (
    !waiting &&
    action.available(context) &&
    unavailableReason(action, context) === null
  )
}

/** The action `key` runs, when it can run now. */
export function shortcutAction(
  key: string,
  context: ActionContext,
  state: { busy: boolean; composing: boolean },
): MailAction | undefined {
  const action = MAIL_ACTIONS.find((each) => each.shortcut === key)
  return action !== undefined && runnable(action, context, state) ? action : undefined
}

/**
 * The action dropping the targets on `into` runs: a move, when it is a folder of their account
 * they can go to; a move to another account, when it is a folder of one holding none of them;
 * else none, and the drop is refused.
 */
export function dropAction(context: ActionContext & { into: Destination }): MailAction | undefined {
  const { actions, into } = context
  const ownAccount = actions.targets.every((target) => target.account_id === into.account)
  if (ownAccount && actions.moveChoices?.some((folder) => isSameFolder(folder.path, into.folder.path)) === true) {
    return mailAction('move')
  }
  if (actions.otherAccounts?.includes(into.account) === true && movableInto(into.folder)) {
    return mailAction('moveToAccount')
  }
  return undefined
}

/** The registry's action `id`; throws for one it lacks. */
export function mailAction(id: string): MailAction {
  const action = MAIL_ACTIONS.find((each) => each.id === id)
  if (action === undefined) {
    throw new Error(`no mail action ${id}`)
  }
  return action
}
