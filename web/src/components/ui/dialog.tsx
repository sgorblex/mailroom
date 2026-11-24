import { useEffect, useRef, type MouseEvent, type ReactElement, type ReactNode } from "react"

import { cn } from "@/lib/utils"

const SIZES = {
  md: "max-w-md",
  lg: "w-full max-w-[min(36rem,calc(100vw-2rem))]",
}

/**
 * A modal over the page: the browser's `<dialog>`, which traps focus, closes on Escape and hides
 * the rest from screen readers. Mounted while open; Escape calls `onCancel`, its own only, not that
 * of a dialog opened from within it (React passes `cancel` up its tree). `lg` is for content that
 * takes the dialog's width rather than setting its own; `className` places one elsewhere (a drawer),
 * and `onClick` sees the clicks on it, its backdrop's included (their target is the dialog itself).
 */
export function Dialog({
  label,
  onCancel,
  size = "md",
  className,
  onClick,
  children,
}: {
  label: string
  onCancel: () => void
  size?: keyof typeof SIZES
  className?: string
  onClick?: (event: MouseEvent<HTMLDialogElement>) => void
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
    // `onClick` is the caller's: a click on the backdrop, or on a link (which Enter clicks too).
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions
    <dialog
      ref={dialog}
      aria-label={label}
      data-size={size}
      onCancel={(event) => {
        if (event.target !== event.currentTarget) {
          return
        }
        event.preventDefault()
        onCancel()
      }}
      onClick={onClick}
      className={cn("m-auto rounded-lg border bg-background p-4 text-foreground backdrop:bg-black/50", SIZES[size], className)}
    >
      {children}
    </dialog>
  )
}
