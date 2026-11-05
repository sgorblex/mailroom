import { useEffect, useRef } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'

import { useTriageActions, type TriageActions } from './triage-actions.ts'
import { useTriage } from './triage-context.ts'

/** Inputs whose keys are not text: a checkbox just ticked still lets `e` archive. */
const KEYLESS_INPUTS = new Set(['checkbox', 'radio', 'button', 'submit', 'reset'])

/** Focus is where keys type text, so they are not shortcuts. */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false
  }
  if (target instanceof HTMLInputElement) {
    return !KEYLESS_INPUTS.has(target.type)
  }
  return target.closest('textarea, select, [contenteditable]:not([contenteditable="false"])') !== null
}

/** A letter key: refused with Alt, which on some layouts types the other keys (`#` is Option+à). */
const LETTER = /^[a-z]$/i

/** Held down, only these repeat: the others would toggle or send again and again. */
const REPEATING = new Set(['j', 'k'])

/**
 * The mail screen's keyboard shortcuts (`docs/requirements/mail-client.md`): j/k move through
 * the list, x selects, e archives, # trashes, s stars, u marks unread, v opens the move picker,
 * Escape closes it or clears the selection. None while typing, none with Ctrl or Meta (but for
 * AltGr, which types characters), and no letter with Alt.
 */
export function useMailShortcuts(): void {
  const triage = useTriage()
  const actions = useTriageActions()
  const navigate = useNavigate()
  // The message in the URL is the current one at once; the open view is set only once it loads.
  const { message } = useSearch({ from: '/mail' })
  const latest = useRef({ triage, actions, navigate, message })
  useEffect(() => {
    latest.current = { triage, actions, navigate, message }
  })

  useEffect(() => {
    function openAt(step: 1 | -1): boolean {
      const { triage: current, navigate: go, message: shown } = latest.current
      const rows = current.visibleRows()
      const at = shown === undefined ? -1 : rows.findIndex((row) => row.ref === shown)
      const next = at === -1 ? (step === 1 ? rows[0] : undefined) : rows[at + step]
      if (next === undefined) {
        return false
      }
      void go({ to: '/mail', search: (previous) => ({ ...previous, message: next.ref }) })
      return true
    }

    function handled(key: string, current: { triage: typeof triage; actions: TriageActions }): boolean {
      const { triage: state, actions: offered } = current
      const run = (action: (() => void) | null | undefined): boolean => {
        if (action === null || action === undefined || state.busy) {
          return false
        }
        action()
        return true
      }
      switch (key) {
        case 'j':
          return openAt(1)
        case 'k':
          return openAt(-1)
        case 'x': {
          const open = state.open
          if (open === null) {
            return false
          }
          state.select([open], !state.selection.has(open.ref))
          return true
        }
        case 'e':
          return run(offered.archive)
        case '#':
          return run(offered.trash)
        case 's':
          return run(offered.star?.run)
        case 'u':
          return run(offered.markUnread)
        case 'v':
          return run(offered.moveChoices === null ? null : () => {
            state.setPicker(true)
          })
        case 'Escape':
          if (state.picker) {
            state.setPicker(false)
            return true
          }
          if (state.selection.size > 0) {
            state.clearSelection()
            return true
          }
          return false
        default:
          return false
      }
    }

    function onKeyDown(event: KeyboardEvent): void {
      const altGraph = event.getModifierState('AltGraph')
      if (
        ((event.ctrlKey || event.metaKey) && !altGraph) ||
        (event.altKey && LETTER.test(event.key)) ||
        (event.repeat && !REPEATING.has(event.key)) ||
        event.defaultPrevented ||
        isTyping(event.target)
      ) {
        return
      }
      if (handled(event.key, latest.current)) {
        event.preventDefault()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])
}
