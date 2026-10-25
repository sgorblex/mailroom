import type { ComponentProps, ReactElement } from 'react'

import { cn } from '@/lib/utils'

/** An on/off button (`role="switch"`); name it with `aria-label` or a `<label>`. */
export function Switch({
  checked,
  onCheckedChange,
  className,
  ...props
}: Omit<ComponentProps<'button'>, 'onClick' | 'role'> & {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}): ReactElement {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => {
        onCheckedChange(!checked)
      }}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-checked:bg-primary',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block size-4 rounded-full bg-background shadow transition-transform',
          checked ? 'translate-x-6' : 'translate-x-1 bg-muted-foreground',
        )}
      />
    </button>
  )
}
