import { useEffect, useRef, type ReactElement, type ReactNode } from "react"

/**
 * A modal over the page: the browser's `<dialog>`, which traps focus, closes on Escape and hides
 * the rest from screen readers. Mounted while open; Escape calls `onCancel`.
 */
export function Dialog({
  label,
  onCancel,
  children,
}: {
  label: string
  onCancel: () => void
  children: ReactNode
}): ReactElement {
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => {
      element?.close()
    }
  }, [])

  return (
    <dialog
      ref={dialog}
      aria-label={label}
      onCancel={(event) => {
        event.preventDefault()
        onCancel()
      }}
      className="m-auto max-w-md rounded-lg border bg-background p-4 text-foreground backdrop:bg-black/50"
    >
      {children}
    </dialog>
  )
}
