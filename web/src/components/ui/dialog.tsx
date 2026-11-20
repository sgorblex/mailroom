import { useEffect, useRef, type ReactElement, type ReactNode } from "react"

import { cn } from "@/lib/utils"

const SIZES = {
  md: "max-w-md",
  lg: "w-full max-w-[min(36rem,calc(100vw-2rem))]",
}

/**
 * A modal over the page: the browser's `<dialog>`, which traps focus, closes on Escape and hides
 * the rest from screen readers. Mounted while open; Escape calls `onCancel`. `lg` is for content that
 * takes the dialog's width rather than setting its own.
 */
export function Dialog({
  label,
  onCancel,
  size = "md",
  children,
}: {
  label: string
  onCancel: () => void
  size?: keyof typeof SIZES
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
      data-size={size}
      onCancel={(event) => {
        event.preventDefault()
        onCancel()
      }}
      className={cn("m-auto rounded-lg border bg-background p-4 text-foreground backdrop:bg-black/50", SIZES[size])}
    >
      {children}
    </dialog>
  )
}
