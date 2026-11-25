import type { RowAction } from '@/api/auth.ts'

import { mailAction, type ActionContext, type MailAction } from './action-registry.ts'

/** The registry's action a row action is now: the one that is possible of a pair (mark read, mark unread). */
function actionId(kind: RowAction, { actions, message, muting }: ActionContext, handledIsStar: boolean): string {
  switch (kind) {
    case 'handled':
      return handledIsStar ? 'star' : 'handled'
    case 'read':
      return actions.read?.seen === true ? 'markRead' : 'markUnread'
    case 'report_spam':
      return 'reportSpam'
    case 'mute':
      return message !== null && muting?.mutedAs(message.ref) !== undefined ? 'unmute' : 'mute'
    default:
      return kind
  }
}

/**
 * The registry's actions a row shows of those the user `chose`: those available on it, once each
 * (Handled is the star's where the account keeps it as the star).
 */
export function rowActions(chose: readonly RowAction[], context: ActionContext, handledIsStar: boolean): MailAction[] {
  const ids = new Set(chose.map((kind) => actionId(kind, context, handledIsStar)))
  return [...ids].map(mailAction).filter((action) => action.available(context))
}
