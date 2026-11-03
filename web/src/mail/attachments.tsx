import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { CalendarDays, Download, Paperclip } from 'lucide-react'

import { messagePartUrl, type MessagePart } from '@/api/mail.ts'

import { formatSize } from './format.ts'
import { MAIL_NS } from './i18n.ts'

/** A message's attachments and calendar invite, each a download. */
export function Attachments({
  messageRef,
  attachments,
  calendar,
}: {
  messageRef: string
  attachments: MessagePart[]
  calendar: MessagePart | null
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <section aria-label={t('attachments.label')} className="flex flex-col gap-2">
      {calendar !== null && (
        <p className="flex items-center gap-2 text-sm">
          <CalendarDays aria-hidden="true" className="size-4 shrink-0" />
          {t('attachments.calendar')}{' '}
          <PartLink messageRef={messageRef} part={calendar} label={t('attachments.downloadInvite')} />
        </p>
      )}
      {attachments.length > 0 && (
        <ul className="flex flex-col divide-y rounded-lg border">
          {attachments.map((part) => (
            <li key={part.part} className="flex items-center gap-2 p-2 text-sm">
              <Paperclip aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
              <PartLink
                messageRef={messageRef}
                part={part}
                label={part.name === '' ? t('attachments.unnamed') : part.name}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** The API names the file (`Content-Disposition`): `download` only says to save it. */
function PartLink({
  messageRef,
  part,
  label,
}: {
  messageRef: string
  part: MessagePart
  label: string
}): ReactElement {
  const { i18n } = useTranslation(MAIL_NS)
  return (
    <>
      <a
        href={messagePartUrl(messageRef, part.part)}
        download
        className="flex min-w-0 items-center gap-1 underline-offset-4 hover:underline"
      >
        <span className="truncate">{label}</span>
        <Download aria-hidden="true" className="size-3.5 shrink-0" />
      </a>
      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
        {`${part.type} · ${formatSize(i18n.language, part.size)}`}
      </span>
    </>
  )
}
