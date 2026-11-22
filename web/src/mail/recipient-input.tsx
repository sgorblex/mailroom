import { useId, type ClipboardEvent, type KeyboardEvent, type ReactElement, type Ref } from 'react'
import { useCombobox } from 'downshift'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'

import type { MailAddress, SuggestedAddress } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { cn } from '@/lib/utils.ts'

import { useAddressSuggestions } from './address-suggestions.ts'
import { addressFull } from './format.ts'
import { MAIL_NS } from './i18n.ts'
import { commitTyped, parseRecipients, withRecipients } from './recipients.ts'

function assignRef<T>(ref: Ref<T> | undefined, node: T | null): void {
  if (typeof ref === 'function') {
    ref(node)
  } else if (ref) {
    ref.current = node
  }
}

export interface Typed {
  text: string
  /** What is left in the field is no address it can take. */
  invalid: boolean
}

/**
 * A recipient field: committed addresses are chips, and only those are the draft's. What is typed
 * stays apart until it is committed, by a separator, Enter or leaving the field, so an address
 * half typed cannot fail an autosave. As the user types, a combobox suggests addresses from the
 * mail index (arrows to move, Enter or Tab to pick one as a chip, Escape to close the list).
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
  const labelId = useId()
  const errorId = useId()
  const suggestions = useAddressSuggestions(typed.text, value)

  function commit(text: string): void {
    const result = commitTyped(value, text)
    if (result.list.length !== value.length) {
      onChange(result.list)
    }
    onTyped({ text: result.text, invalid: result.invalid })
  }

  function pick(item: SuggestedAddress): void {
    const { list, over } = withRecipients(value, [{ name: item.name, address: item.address }])
    if (list.length !== value.length) {
      onChange(list)
    }
    onTyped(over.length > 0 ? { text: item.address, invalid: true } : { text: '', invalid: false })
  }

  const { isOpen, highlightedIndex, getInputProps, getMenuProps, getItemProps, selectItem } = useCombobox<SuggestedAddress>({
    items: suggestions,
    inputId: id,
    labelId,
    itemToString: (item) => item?.address ?? '',
    itemToKey: (item) => item?.address,
    inputValue: typed.text,
    selectedItem: null,
    onSelectedItemChange: ({ selectedItem }) => {
      if (selectedItem !== null) {
        pick(selectedItem)
      }
    },
    stateReducer: (state, { type, changes }) => {
      // Leaving the field commits what was typed, never the highlighted suggestion.
      if (type === useCombobox.stateChangeTypes.InputBlur) {
        return { ...changes, selectedItem: state.selectedItem }
      }
      return changes
    },
  })
  const open = isOpen && suggestions.length > 0

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    const highlighted = open ? suggestions[highlightedIndex] : undefined
    if (highlighted !== undefined && (event.key === 'Enter' || event.key === 'Tab')) {
      event.preventDefault()
      Object.assign(event.nativeEvent, { preventDownshiftDefault: true })
      selectItem(highlighted)
    } else if (event.key === 'Escape' && open) {
      // Closes the list only, not what holds the composer.
      event.preventDefault()
      event.stopPropagation()
    } else if (event.key === 'Enter') {
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

  const { ref: comboboxRef, ...inputProps } = getInputProps({
    onChange: (event) => {
      onTyped({ text: event.currentTarget.value, invalid: false })
    },
    onKeyDown,
    onPaste,
    onBlur: () => {
      commit(typed.text)
    },
  })

  return (
    <div className="flex flex-col gap-1">
      <label id={labelId} htmlFor={id} className="text-sm text-muted-foreground">
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
      <div className="relative">
        <Input
          {...inputProps}
          ref={(node) => {
            assignRef(comboboxRef, node)
            assignRef(ref, node)
          }}
          type="text"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={typed.invalid}
          aria-describedby={typed.invalid ? errorId : undefined}
        />
        <ul
          {...getMenuProps({ 'aria-label': t('compose.suggestions', { field: label }), 'aria-labelledby': undefined })}
          className={cn(
            'absolute top-full right-0 left-0 z-20 mt-1 max-h-72 overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md',
            !open && 'hidden',
          )}
        >
          {open &&
            suggestions.map((item, index) => (
              <li
                key={item.address}
                {...getItemProps({
                  item,
                  index,
                  // The field keeps the focus: a click picks, it does not commit what was typed.
                  onMouseDown: (event) => {
                    event.preventDefault()
                  },
                })}
                className={cn(
                  'flex cursor-default flex-col rounded-sm px-2 py-1.5 text-sm',
                  index === highlightedIndex && 'bg-accent text-accent-foreground',
                )}
              >
                {item.name !== '' && <span className="truncate">{item.name}</span>}
                <span className={cn('truncate', item.name !== '' && 'text-xs text-muted-foreground')}>{item.address}</span>
              </li>
            ))}
        </ul>
      </div>
      {typed.invalid && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {t('compose.invalidRecipient')}
        </p>
      )}
    </div>
  )
}
