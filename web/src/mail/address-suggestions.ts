import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { suggestAddresses, type MailAddress, type SuggestedAddress } from '@/api/mail.ts'

import { addressesKey } from './queries.ts'
import { isDeliverable } from './recipients.ts'

/** How long typing pauses before the field asks for suggestions. */
export const SUGGEST_DELAY_MS = 200
/** The longest text the server takes. */
const MAX_PREFIX = 200

function useSettled(value: string, delay: number): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => {
      setSettled(value)
    }, delay)
    return () => {
      clearTimeout(timer)
    }
  }, [value, delay])
  return settled
}

/** Whether `suggestion` is one the server would give for `typed`: by a prefix of its address, its name or a word of the name. */
export function matchesTyped(suggestion: SuggestedAddress, typed: string): boolean {
  const needle = typed.trim().toLowerCase()
  const name = suggestion.name.toLowerCase()
  return suggestion.address.toLowerCase().startsWith(needle) || name.startsWith(needle) || name.includes(` ${needle}`)
}

/**
 * The addresses the index suggests for `text` once typing pauses, but those already in the field
 * (`taken`) and any the server would refuse; none for an empty or over-long text. While the answer
 * for a newer text is on its way, the last one stays, narrowed to what still matches, so the list
 * does not close and reopen with each key.
 */
export function useAddressSuggestions(text: string, taken: readonly MailAddress[]): SuggestedAddress[] {
  const typed = text.trim()
  const prefix = useSettled(typed, SUGGEST_DELAY_MS)
  const query = useQuery({
    queryKey: addressesKey(prefix),
    queryFn: ({ signal }) => suggestAddresses(prefix, signal),
    enabled: prefix !== '' && prefix.length <= MAX_PREFIX,
    staleTime: 60_000,
    retry: false,
    placeholderData: keepPreviousData,
  })
  if (typed === '' || typed.length > MAX_PREFIX || query.data === undefined) {
    return []
  }
  const held = new Set(taken.map((each) => each.address.toLowerCase()))
  return query.data.filter((each) => matchesTyped(each, typed) && isDeliverable(each.address) && !held.has(each.address.toLowerCase()))
}
