import type { MessageView } from '@/api/mail.ts'

type MailingList = NonNullable<MessageView['headers']['mailing_list']>

/** How the Unsubscribe button unsubscribes, the first the message allows; never on its own. */
export type UnsubscribeWay =
  /** RFC 8058's POST, which the server checked: a confirm dialog, then its result. */
  | { kind: 'oneClick'; host: string }
  /** A draft to the `mailto:` address, in the composer; `allowed` false when the account's send mode is off. */
  | { kind: 'mail'; allowed: boolean }
  /** The list's own page, in a new tab. */
  | { kind: 'link'; url: string }

/** `value` when it is an absolute `https:` URL, else `null`: the sender's words, never trusted to be one. */
export function httpsUrl(value: string | null): URL | null {
  if (value === null) {
    return null
  }
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname !== '' ? url : null
  } catch {
    return null
  }
}

/** The list's name, else its id, else `null`. */
export function listName(list: MailingList): string | null {
  return list.name ?? list.id
}

export function unsubscribeWay(view: MessageView, canCompose: boolean): UnsubscribeWay | null {
  const list = view.headers.mailing_list
  if (list === null) {
    return null
  }
  const https = httpsUrl(list.unsubscribe_https)
  if (view.one_click_unsubscribe && https !== null) {
    return { kind: 'oneClick', host: https.hostname }
  }
  if (list.unsubscribe_mailto !== null && (canCompose || https === null)) {
    return { kind: 'mail', allowed: canCompose }
  }
  return https === null ? null : { kind: 'link', url: https.href }
}
