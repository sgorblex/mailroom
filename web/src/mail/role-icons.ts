import { Archive, FilePen, Inbox, Layers, Mails, Send, ShieldAlert, Star, Trash2, type LucideIcon } from 'lucide-react'

import type { FolderRole } from '@/api/mail.ts'

/** Each role folder's icon, in the folder tree and on the rail. */
export const ROLE_ICONS: Record<FolderRole, LucideIcon> = {
  inbox: Inbox,
  sent: Send,
  drafts: FilePen,
  trash: Trash2,
  junk: ShieldAlert,
  archive: Archive,
  all: Mails,
  flagged: Star,
}

/** *All inboxes*' icon, a stack, set apart from the Inbox folder's. */
export const ALL_INBOXES_ICON: LucideIcon = Layers
