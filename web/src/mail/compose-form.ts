import type { KeptPart, KeptShownPart, MailAddress, MessagePart, ShownPart } from '@/api/mail.ts'

/** A file still to upload; it stops being one when a save lists it in `parts`. */
export interface TrayFile {
  kind: 'file'
  id: string
  file: File
}

/** A part kept from another message, or from the draft's own saved version. */
export type TrayPart = { kind: 'part'; id: string } & KeptPart

export type TrayItem = TrayFile | TrayPart

/** An image the HTML shows in place as `cid:<contentId>`: a file to upload, or a part kept. */
export type InlineItem = (TrayFile | TrayPart) & { contentId: string }

export interface ComposeForm {
  /** An identity's address; `null` for the account's default. */
  from: string | null
  to: MailAddress[]
  cc: MailAddress[]
  bcc: MailAddress[]
  subject: string
  /** The body; in rich text, what the editor shows as text, which a save does not send. */
  text: string
  /** The body as HTML, its images as `cid:`; `null` in plain text. */
  html: string | null
  inline: InlineItem[]
  tray: TrayItem[]
}

export function isPart<T extends TrayItem>(item: T): item is T & TrayPart {
  return item.kind === 'part'
}

export function isFile<T extends TrayItem>(item: T): item is T & TrayFile {
  return item.kind === 'file'
}

export function trayPart(part: KeptPart): TrayPart {
  return { kind: 'part', id: `${part.ref}:${part.part}`, ...part }
}

export function inlinePart(part: KeptShownPart): InlineItem {
  return { kind: 'part', id: `${part.ref}:${part.part}`, contentId: part.content_id, ...part }
}

/** A Content-ID for an image added here: unique, and nothing a `cid:` URL would need to escape. */
export function newContentId(): string {
  return `${crypto.randomUUID()}@mailroom`
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** Plain text as the rich editor's HTML: a paragraph a line. */
export function plainToHtml(text: string): string {
  return text
    .split('\n')
    .map((line) => `<p>${escapeHtml(line)}</p>`)
    .join('')
}

const CID_SOURCE = /\bsrc="cid:([^"]+)"/g

/** The inline images `html` shows. */
export function shownImages(html: string | null, inline: InlineItem[]): InlineItem[] {
  if (html === null) {
    return []
  }
  const shown = new Set(Array.from(html.matchAll(CID_SOURCE), (match) => match[1]))
  return inline.filter((item) => shown.has(item.contentId))
}

/**
 * The inline images once a version showing `shown` is saved as `ref`: each is matched by its
 * Content-ID, which this client made unique, so an upload becomes the part it was saved as. A part
 * of an earlier version that this one did not show goes, since that version is gone with it; one of
 * `alive` (the forwarded message) stays, for an undo to bring back.
 */
export function reconcileInline(current: InlineItem[], ref: string, shown: ShownPart[], alive: string | null): InlineItem[] {
  return current.flatMap((item) => {
    const part = shown.find((each) => each.content_id === item.contentId)
    if (part !== undefined) {
      return [inlinePart({ ...part, ref })]
    }
    return isPart(item) && item.ref !== ref && item.ref !== alive ? [] : [item]
  })
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

/** Uploads in one save, as the server counts them. */
export const MAX_FILES = 10
const MIB = 2 ** 20

/** The limit adding `files` to what a save already carries (`items`) would break, if any. */
export function uploadProblem(items: TrayItem[], files: File[], maxMb: number): 'count' | 'size' | null {
  if (items.filter(isFile).length + files.length > MAX_FILES) {
    return 'count'
  }
  return trayBytes(items) + files.reduce((total, file) => total + file.size, 0) > maxMb * MIB ? 'size' : null
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
    form.to.length + form.cc.length + form.bcc.length + form.tray.length + shownImages(form.html, form.inline).length === 0 &&
    form.subject === '' &&
    form.text === ''
  )
}
