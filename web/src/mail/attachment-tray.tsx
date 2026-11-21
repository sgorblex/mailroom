import { useId, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Paperclip, X } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'

import { itemName, itemSize, MAX_FILES, uploadProblem, type TrayItem } from './compose-form.ts'
import { formatSize } from './format.ts'
import { MAIL_NS } from './i18n.ts'

/** What the draft attaches: files to upload and parts it keeps. Limits are said before any upload. */
export function AttachmentTray({
  items,
  inline,
  maxMb,
  onAdd,
  onRemove,
}: {
  items: TrayItem[]
  /** The images the body shows, which count towards the limits too. */
  inline: TrayItem[]
  maxMb: number
  onAdd: (files: File[]) => void
  onRemove: (item: TrayItem) => void
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const id = useId()
  const errorId = useId()
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  function add(files: File[]): void {
    const problem = uploadProblem([...items, ...inline], files, maxMb)
    if (problem === 'count') {
      setError(t('compose.tooManyFiles', { count: MAX_FILES }))
    } else if (problem === 'size') {
      setError(t('compose.attachmentsTooLarge', { limit: maxMb }))
    } else {
      setError(null)
      onAdd(files)
    }
    // The same file can be chosen again, after removing it.
    if (input.current !== null) {
      input.current.value = ''
    }
  }

  return (
    <section aria-label={t('attachments.label')} className="flex flex-col gap-2">
      {items.length > 0 && (
        <ul className="flex flex-col divide-y rounded-lg border">
          {items.map((item) => {
            const name = itemName(item) === '' ? t('attachments.unnamed') : itemName(item)
            return (
              <li key={item.id} className="flex items-center gap-2 p-2 text-sm">
                <Paperclip aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{name}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{formatSize(i18n.language, itemSize(item))}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  aria-label={t('compose.removeAttachment', { name })}
                  onClick={() => {
                    onRemove(item)
                  }}
                >
                  <X aria-hidden="true" />
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      <div>
        <input
          ref={input}
          id={id}
          type="file"
          multiple
          className="peer sr-only"
          aria-describedby={error === null ? undefined : errorId}
          onChange={(event) => {
            const files = [...(event.target.files ?? [])]
            if (files.length > 0) {
              add(files)
            }
          }}
        />
        <label
          htmlFor={id}
          className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-accent peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50"
        >
          <Paperclip aria-hidden="true" className="size-4" />
          {t('compose.attach')}
        </label>
      </div>
      {error !== null && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}
