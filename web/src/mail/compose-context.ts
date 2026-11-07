import { createContext, useContext, type Dispatch, type SetStateAction } from 'react'

import type { Account } from '@/api/accounts.ts'
import type { SendResult } from '@/api/mail.ts'

import type { ComposeSession, ComposeSource } from './compose-session.ts'

/** What the open editor offers the screen around it. */
export interface EditorHandle {
  /** The latest version's ref. */
  ref: () => string | null
  /** Saves what is unsaved, to leave the draft; false when it must stay open. */
  leave: () => Promise<boolean>
  focus: () => void
}

/** How the last send ended, once its composer has closed. */
export type SendNotice = Pick<SendResult, 'filed' | 'rejected' | 'note_codes'>

/** Something for screen readers to hear; `urgent` is for a failure. */
export interface Announcement {
  text: string
  urgent: boolean
}

export interface Compose {
  session: ComposeSession | null
  /** The editor is what the third pane shows; else it waits, mounted, behind a message. */
  shown: boolean
  /** Opens a composer, leaving the one open if it can be left. */
  start: (source: ComposeSource) => void
  /** Closes the composer, whose draft is already dealt with. */
  close: () => void
  reveal: () => void
  /** Reveals the editor from a control that goes away with that, handing it the focus. */
  backToDraft: () => void
  hide: () => void
  account: (id: string) => Account | undefined
  /** The account's send mode is not `off`. */
  canCompose: (id: string) => boolean
  attach: (handle: EditorHandle) => () => void
  /** The editor has its `compose_id` and account: the URL can name it. */
  identified: (composeId: string, account: string) => void
  /** The account's drafts changed: what lists them is out of date. */
  mailChanged: () => void
  /** Bumped by `mailChanged`. */
  revision: number
  /** Closes the composer of a delivered message and says how it went. */
  finishSent: (notice: SendNotice) => void
  notice: SendNotice | null
  dismissNotice: () => void
  announcement: Announcement | null
  announce: Dispatch<SetStateAction<Announcement | null>>
}

export const ComposeContext = createContext<Compose | null>(null)

export function useCompose(): Compose {
  const compose = useContext(ComposeContext)
  if (compose === null) {
    throw new Error('useCompose outside a ComposeProvider')
  }
  return compose
}
