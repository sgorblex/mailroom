import { useEffect, useRef } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'

import type { AccountId } from '@/api/accounts.ts'

import { MAIL_ACTIONS, shortcutAction, type ActionContext, type MailAction } from './action-registry.ts'
import { useCompose, type Compose } from './compose-context.ts'
import { NO_TRIAGE_ACTIONS, useTriageActions, type TriageActions } from './triage-actions.ts'
import { useTriage, type Triage } from './triage-context.ts'

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

interface SheetEntry {
  key: string
  label: string
  /** How the sheet writes the key, where not as `key` (a key in the `mail` namespace). */
  keyName?: string
}

/** The keys the mail screen handles itself, beside the registry's, with their names in the shortcut sheet. */
const NAVIGATION_KEYS: readonly SheetEntry[] = [
  { key: 'j', label: 'shortcuts.next' },
  { key: 'k', label: 'shortcuts.previous' },
  { key: 'x', label: 'shortcuts.select' },
  { key: 'Escape', label: 'shortcuts.escape', keyName: 'shortcuts.escapeKey' },
  { key: '?', label: 'shortcuts.sheet' },
  { key: 'Control+K', label: 'shortcuts.palette', keyName: 'shortcuts.paletteKey' },
]

/** No targets: the names an action gives itself whatever it acts on. */
const NO_CONTEXT: ActionContext = {
  actions: NO_TRIAGE_ACTIONS,
  message: null,
  account: null,
  compose: { start: () => undefined, canCompose: () => true },
  openPicker: () => undefined,
}

function registryKeys(groups: readonly MailAction['group'][]): SheetEntry[] {
  return MAIL_ACTIONS.flatMap((action) =>
    action.shortcut !== undefined && groups.includes(action.group)
      ? [{ key: action.shortcut, label: action.sheetLabel ?? action.label(NO_CONTEXT) }]
      : [],
  )
}

/** The sheet's groups: the screen's own keys, then the registry's, which the toolbar and menus name too. */
export const SHEET_GROUPS: readonly { title: string; entries: SheetEntry[] }[] = [
  { title: 'shortcuts.moving', entries: [...NAVIGATION_KEYS] },
  { title: 'shortcuts.writing', entries: registryKeys(['reply', 'compose']) },
  { title: 'shortcuts.acting', entries: registryKeys(['triage']) },
]

/**
 * What the screen's keys and its command palette run an action with: the selection, else the open
 * message; a reply on the open one, a new message on `account`; a picker opens.
 */
export function screenContext(triage: Triage, actions: TriageActions, compose: Compose, account: AccountId): ActionContext {
  return {
    actions,
    message: triage.open,
    account,
    compose,
    openPicker: (picker) => {
      if (picker === 'move') {
        triage.setPicker(true)
      } else {
        triage.setAccountPicker(true)
      }
    },
  }
}

/**
 * Ctrl+K, or ⌘+K, calls `onToggle` (the command palette): a key of its own, as the others take
 * none with Ctrl or Meta. Not while typing in the composer, nor over another dialog.
 */
export function usePaletteShortcut(onToggle: () => void): void {
  const latest = useRef(onToggle)
  useEffect(() => {
    latest.current = onToggle
  })
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      const dialog = document.querySelector('dialog[open]')
      if (
        !(event.ctrlKey || event.metaKey) ||
        event.altKey ||
        event.shiftKey ||
        event.key.toLowerCase() !== 'k' ||
        event.repeat ||
        event.defaultPrevented ||
        (isTyping(event.target) && event.target instanceof Element && event.target.closest('[data-composer]') !== null) ||
        (dialog !== null && dialog.querySelector('[data-palette]') === null)
      ) {
        return
      }
      event.preventDefault()
      latest.current()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [])
}

/**
 * The mail screen's keyboard shortcuts (`docs/requirements/mail-client.md`): j/k move through
 * the list, x selects, Escape closes a picker or clears the selection, `?` calls `onHelp` (the
 * shortcut sheet); the other keys are the action registry's, on the selection or the open message
 * (a reply on the open one, a new message on `accountId`). None while typing, none with Ctrl or
 * Meta (but for AltGr, which types characters), and no letter with Alt.
 */
export function useMailShortcuts(accountId: AccountId, onHelp: () => void): void {
  const triage = useTriage()
  const actions = useTriageActions()
  const compose = useCompose()
  const navigate = useNavigate()
  // The message in the URL is the current one at once; the open view is set only once it loads.
  const { message } = useSearch({ from: '/mail' })
  const latest = useRef({ triage, actions, navigate, message, compose, accountId, onHelp })
  useEffect(() => {
    latest.current = { triage, actions, navigate, message, compose, accountId, onHelp }
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

    function handled(key: string, current: typeof latest.current): boolean {
      const { triage: state, actions: offered, compose: writer } = current
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
        case '?':
          current.onHelp()
          return true
        case 'Escape':
          if (state.picker) {
            state.setPicker(false)
            return true
          }
          if (state.accountPicker) {
            state.setAccountPicker(false)
            return true
          }
          if (state.selection.size > 0) {
            state.clearSelection()
            return true
          }
          return false
      }
      const context = screenContext(state, offered, writer, current.accountId)
      const action = shortcutAction(key, context, { busy: state.busy, composing: writer.session !== null })
      if (action === undefined) {
        return false
      }
      action.run(context)
      return true
    }

    function onKeyDown(event: KeyboardEvent): void {
      const altGraph = event.getModifierState('AltGraph')
      if (
        ((event.ctrlKey || event.metaKey) && !altGraph) ||
        (event.altKey && LETTER.test(event.key)) ||
        (event.repeat && !REPEATING.has(event.key)) ||
        event.defaultPrevented ||
        isTyping(event.target) ||
        // A modal leaves the page behind it out of reach, and Escape to the dialog; an open menu
        // takes the keys too.
        document.querySelector('dialog[open], [role="menu"]') !== null
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
