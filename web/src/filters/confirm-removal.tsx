import { useEffect, useRef, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button.tsx'

import { FILTERS_NS } from './i18n.ts'

/** "Remove …?" with its confirm button, which takes focus when it shows; ignored while `pending`. */
export function ConfirmRemoval({
  question,
  pending,
  onConfirm,
  onCancel,
}: {
  question: string
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const confirm = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    confirm.current?.focus()
  }, [])

  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-sm">{question}</p>
      <Button
        ref={confirm}
        variant="destructive"
        size="sm"
        aria-disabled={pending}
        className="aria-disabled:opacity-50"
        onClick={() => {
          if (!pending) {
            onConfirm()
          }
        }}
      >
        {t('filters.confirmRemove')}
      </Button>
      <Button variant="ghost" size="sm" onClick={onCancel}>
        {t('filters.cancel')}
      </Button>
    </div>
  )
}
