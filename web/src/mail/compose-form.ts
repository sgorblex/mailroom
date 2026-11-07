import type { KeptPart, MailAddress, MessagePart } from '@/api/mail.ts'

/** A file still to upload; it stops being one when a save lists it in `parts`. */
export interface TrayFile {
  kind: 'file'
  id: string
  file: File
}

/** A part kept from another message, or from the draft's own saved version. */
export type TrayPart = { kind: 'part'; id: string } & KeptPart

export type TrayItem = TrayFile | TrayPart

export interface ComposeForm {
  /** An identity's address; `null` for the account's default. */
  from: string | null
  to: MailAddress[]
  cc: MailAddress[]
  bcc: MailAddress[]
  subject: string
  text: string
  tray: TrayItem[]
}

export function isPart(item: TrayItem): item is TrayPart {
  return item.kind === 'part'
}

export function isFile(item: TrayItem): item is TrayFile {
  return item.kind === 'file'
}

export function trayPart(part: KeptPart): TrayPart {
  return { kind: 'part', id: `${part.ref}:${part.part}`, ...part }
}

export function itemName(item: TrayItem): string {
  return isFile(item) ? item.file.name : item.name
}

export function itemSize(item: TrayItem): number {
  return isFile(item) ? item.file.size : item.size
}

export function trayBytes(tray: TrayItem[]): number {
  return tray.reduce((total, item) => total + itemSize(item), 0)
}

/** `parts` less the ones named by what `saved` held and the tray no longer does. */
function withoutNamed(parts: MessagePart[], saved: TrayItem[], now: Set<string>): MessagePart[] {
  const remaining = [...parts]
  for (const item of saved) {
    if (!now.has(item.id)) {
      const at = remaining.findIndex((part) => part.name === itemName(item))
      if (at !== -1) {
        remaining.splice(at, 1)
      }
    }
  }
  return remaining
}

/**
 * The tray once a version with `parts` is saved: its parts stand for everything that version
 * carried (`saved`). What the user removed meanwhile stays removed, and what they added stays,
 * for the next save to carry.
 */
export function reconcileTray(current: TrayItem[], saved: TrayItem[], ref: string, parts: MessagePart[]): TrayItem[] {
  const now = new Set(current.map((item) => item.id))
  const then = new Set(saved.map((item) => item.id))
  // The server lists what was kept, then the uploads, so position tells parts apart: names repeat,
  // and it may have changed one.
  const sent = [...saved.filter(isPart), ...saved.filter(isFile)]
  const remaining = sent.length === parts.length ? parts.filter((_, at) => now.has(sent[at]?.id ?? '')) : withoutNamed(parts, saved, now)
  return [...remaining.map((part) => trayPart({ ...part, ref })), ...current.filter((item) => !then.has(item.id))]
}

/** Nothing typed or attached: closing it leaves no draft behind. */
export function isBlank(form: ComposeForm): boolean {
  return (
    form.to.length + form.cc.length + form.bcc.length + form.tray.length === 0 &&
    form.subject === '' &&
    form.text === ''
  )
}
