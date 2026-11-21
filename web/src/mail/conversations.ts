import type { MessageItem } from '@/api/mail.ts'

import type { Action, Flags, Target } from './triage-context.ts'

/** Whether the row stands for a conversation of several messages. */
export function isConversation(target: Pick<Target, 'conversation'>): boolean {
  return (target.conversation?.count ?? 1) > 1
}

/** What an action on the row takes: every message of its conversation, else itself. */
export function refsOf(target: Target): readonly string[] {
  return target.conversation?.refs ?? [target.ref]
}

/** The folder the message `ref` of the row was listed under, where an action on it acts. */
export function listedFolderOf(target: Target, ref: string): string | null | undefined {
  return target.conversation?.listed_folders[ref] ?? (ref === target.ref ? target.listed_folder : undefined)
}

/**
 * The flags the row does not say for every message of its conversation: read when some are
 * unread and some not; starred and To sort unless it is the one message (starred: unless none is).
 */
export function mixedFlags(target: Target): (keyof Flags)[] {
  const conversation = target.conversation
  if (conversation === undefined || conversation.count <= 1) {
    return []
  }
  const mixed: (keyof Flags)[] = ['to_sort']
  if (conversation.unread > 0 && conversation.unread < conversation.count) {
    mixed.push('seen')
  }
  if (conversation.flagged) {
    mixed.push('flagged')
  }
  return mixed
}

/**
 * A listed message as its row: in a grouped list, its conversation's, read while none of it is
 * unread and starred when one of it is.
 */
export function listRow(message: MessageItem): MessageItem {
  const conversation = message.conversation
  if (conversation === undefined || conversation.count <= 1) {
    return message
  }
  return { ...message, flags: { ...message.flags, seen: conversation.unread === 0, flagged: conversation.flagged } }
}

/** The URL's open message as the row opens it: a conversation's with `thread`. */
export function opened(target: Target): { message: string; thread: string | undefined } {
  return { message: target.ref, thread: isConversation(target) ? target.ref : undefined }
}

/** A conversation of several whose newest message is from a sender new to it. */
export function newSenderIn(target: Target): boolean {
  return isConversation(target) && target.conversation?.latest_from_new_sender === true
}

/** Whether `action` on `targets` asks first: a trash, archive, move or spam report of such a conversation. */
export function asksFirst(action: Action, targets: readonly Target[]): boolean {
  const moving = action.kind === 'trash' || action.kind === 'archive' || action.kind === 'move' || action.kind === 'junk'
  return moving && targets.some(newSenderIn)
}

/** The row as the message it shows alone, without its conversation. */
export function shownOnly(target: Target): Target {
  const { conversation, ...shown } = target
  return conversation === undefined ? target : shown
}
