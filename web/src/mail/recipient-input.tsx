import { useId, type ClipboardEvent, type KeyboardEvent, type ReactElement, type Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

import type { MailAddress } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

import { addressFull } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { commitTyped, parseRecipients } from './recipients.ts'

export interface Typed {
  text: string
  /** What is left in the field is no address it can take. */
  invalid: boolean
}

/**
 * A recipient field: committed addresses are chips, and only those are the draft's. What is typed
 * stays apart until it is committed, by a separator, Enter or leaving the field, so an address
 * half typed cannot fail an autosave.
 */
export function RecipientInput({
  label,
  value,
  typed,
  onChange,
  onTyped,
  ref,
}: {
  label: string
  value: MailAddress[]
  typed: Typed
  onChange: (value: MailAddress[]) => void
  onTyped: (typed: Typed) => void
  ref?: Ref<HTMLInputElement>
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const id = useId()
  const errorId = useId()

  function commit(text: string): void {
    const result = commitTyped(value, text)
    if (result.list.length !== value.length) {
      onChange(result.list)
    }
    onTyped({ text: result.text, invalid: result.invalid })
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === 'Enter') {
      event.preventDefault()
      commit(typed.text)
    } else if (event.key === 'Backspace' && typed.text === '' && value.length > 0) {
      onChange(value.slice(0, -1))
    } else if ((event.key === ',' || event.key === ';') && parseRecipients(typed.text).invalid.length === 0) {
      // Else it may be the comma of a quoted name, still being typed.
      event.preventDefault()
      commit(typed.text)
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>): void {
    const pasted = event.clipboardData.getData('text')
    if (/[,;\n]/.test(pasted)) {
      event.preventDefault()
      commit(`${typed.text}${pasted}`)
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm text-muted-foreground">
        {label}
      </label>
      {value.length > 0 && (
        <ul aria-label={t('compose.recipientsOf', { field: label })} className="flex flex-wrap gap-1">
          {value.map((address) => (
            <li key={address.address} className="flex max-w-full items-center gap-1 rounded-md border py-0.5 pr-0.5 pl-2 text-sm">
              <span className="min-w-0 truncate">{addressFull(address)}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label={t('compose.removeRecipient', { address: address.address })}
                onClick={() => {
                  onChange(value.filter((each) => each !== address))
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Input
        ref={ref}
        id={id}
        type="text"
        autoComplete="off"
        spellCheck={false}
        value={typed.text}
        aria-invalid={typed.invalid}
        aria-describedby={typed.invalid ? errorId : undefined}
        onChange={(event) => {
          onTyped({ text: event.target.value, invalid: false })
        }}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={() => {
          commit(typed.text)
        }}
      />
      {typed.invalid && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {t('compose.invalidRecipient')}
        </p>
      )}
    </div>
  )
}
