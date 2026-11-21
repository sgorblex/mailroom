import type { AccountId } from '@/api/accounts.ts'
import type { AccountNotifications } from '@/api/mail.ts'

import { isSameFolder } from './triage-context.ts'

export type NotificationAccess = NotificationPermission | 'unsupported'

/**
 * Whether each `Notification` class met lets a page construct one: Chrome for Android has the
 * API but throws, leaving notifications to a service worker, which Mailroom does not have.
 */
const constructs = new WeakMap<object, boolean>()

/**
 * Whether this browser shows Mailroom's notifications: `default` until the user is asked. The
 * constructor is tried only before the permission is granted, when nothing can show.
 */
export function notificationAccess(): NotificationAccess {
  if (typeof Notification === 'undefined') {
    return 'unsupported'
  }
  if (!constructs.has(Notification) && Notification.permission !== 'granted') {
    try {
      new Notification('').close()
      constructs.set(Notification, true)
    } catch {
      constructs.set(Notification, false)
    }
  }
  return constructs.get(Notification) === false ? 'unsupported' : Notification.permission
}

/** Whether a notification can show now, without trying the constructor. */
export function mayNotify(): boolean {
  return typeof Notification !== 'undefined' && Notification.permission === 'granted' && constructs.get(Notification) !== false
}

/** A notification shown, or null when this browser refuses to construct one (from then on `unsupported`). */
export function showNotification(title: string, options: NotificationOptions): Notification | null {
  try {
    return new Notification(title, options)
  } catch {
    constructs.set(Notification, false)
    return null
  }
}

/** Whether `folder`'s new mail is notified: an account the choice does not list has INBOX alone. */
export function notifies(chosen: readonly AccountNotifications[], account: AccountId, folder: string): boolean {
  const folders = chosen.find((each) => each.account_id === account)?.folders ?? ['INBOX']
  return folders.some((each) => isSameFolder(each, folder))
}
