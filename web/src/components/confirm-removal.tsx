import { useEffect, useRef, type ReactElement } from 'react'

import { Button } from '@/components/ui/button.tsx'

/**
 * "Remove …?" with its confirm button, ignored while `pending`. Cancel takes focus when it shows,
 * so Enter backs out rather than removes. The caller words it, in its own namespace: the question
 * and both buttons.
 */
export function ConfirmRemoval({
  question,
  confirmLabel,
  cancelLabel,
  pending,
  onConfirm,
  onCancel,
}: {
  question: string
  confirmLabel: string
  cancelLabel: string
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}): ReactElement {
  const cancel = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancel.current?.focus()
  }, [])

  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-sm">{question}</p>
      <Button
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
        {confirmLabel}
      </Button>
      <Button ref={cancel} variant="ghost" size="sm" onClick={onCancel}>
        {cancelLabel}
      </Button>
    </div>
  )
}
