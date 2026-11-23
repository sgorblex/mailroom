import { act, cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LG_QUERY } from '@/lib/media-query.ts'
import { HOME, messageItem, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { isCollapsed, toggled } from './folder-pane.ts'

const STORED = 'mailroom.ui.mail.folderPane'

function state(): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 3 },
        { path: 'Sent Messages', role: 'sent', unread: 0, total: 1 },
        { path: 'Projects', role: null, unread: 1, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 3)],
      '1/Sent Messages': [messageItem(WORK, 'Sent Messages', 1, { subject: 'Sent one' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1)],
    },
    views: {},
  }
}

/** A window `lg` wide, and `xl` too when `roomy`. */
function windowOf(roomy: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === LG_QUERY || roomy,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}

const pane = (): HTMLElement | null => screen.queryByRole('navigation', { name: 'Accounts and folders' })
const rail = (): Promise<HTMLElement> => screen.findByRole('navigation', { name: 'Main folders' })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the folder pane', () => {
  it('folds to a rail with the button and opens again, kept for this device', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await user.click(await screen.findByRole('button', { name: 'Fold the folder pane' }))

    const icons = await rail()
    expect(pane()).not.toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBe('"collapsed"')
    const open = within(icons).getByRole('button', { name: 'Open the folder pane' })
    expect(open).toHaveFocus()
    expect(open).toHaveAttribute('aria-keyshortcuts', '[')
    expect(open).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById(open.getAttribute('aria-controls') ?? '')).toHaveAttribute('aria-label', 'Accounts and folders')
    // The main folders, with their unread counts in their names; not the others.
    expect(await within(icons).findByRole('link', { name: 'Inbox, 3 unread' })).toHaveAttribute('aria-current', 'page')
    expect(within(icons).getByRole('link', { name: 'Sent' })).toBeInTheDocument()
    const allInboxes = within(icons).getByRole('link', { name: /^All inboxes/ })
    // Its own icon, not the Inbox folder's.
    expect(allInboxes.querySelector('svg')).toHaveClass('lucide-layers')
    expect(within(icons).getByRole('link', { name: /^Inbox/ }).querySelector('svg')).toHaveClass('lucide-inbox')
    expect(within(icons).queryByRole('link', { name: /Projects/ })).not.toBeInTheDocument()

    cleanup()
    renderMailApp('/mail')
    await user.click(within(await rail()).getByRole('button', { name: 'Open the folder pane' }))
    expect(pane()).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Main folders' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fold the folder pane' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Fold the folder pane' })).toHaveAttribute('aria-expanded', 'true')
    expect(window.localStorage.getItem(STORED)).toBe('"auto"')
  })

  it('keeps the focus on the toggle when [ folds or opens it from within', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('treeitem', { name: 'Sent' })
    act(() => {
      screen.getByRole('treeitem', { name: 'Sent' }).focus()
    })
    await user.keyboard('[[')
    const open = within(await rail()).getByRole('button', { name: 'Open the folder pane' })
    expect(open).toHaveFocus()
    expect(pane()).not.toBeInTheDocument()

    await user.keyboard('[[')
    expect(screen.getByRole('button', { name: 'Fold the folder pane' })).toHaveFocus()
  })

  it('folds and opens with [, but not while typing', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('button', { name: 'Fold the folder pane' })
    await user.click(screen.getByRole('searchbox'))
    await user.keyboard('[[')
    expect(pane()).toBeInTheDocument()

    await user.click(document.body)
    await user.keyboard('[[')
    await rail()
    expect(pane()).not.toBeInTheDocument()
    await user.keyboard('[[')
    expect(pane()).toBeInTheDocument()
  })

  it('is folded on its own below xl, until opened there', async () => {
    const user = userEvent.setup()
    windowOf(false)
    stubMailApi(state())
    renderMailApp('/mail')
    await user.click(within(await rail()).getByRole('button', { name: 'Open the folder pane' }))
    expect(pane()).toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBe('"expanded"')
  })

  it('peeks over the list while hovered', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(STORED, '"collapsed"')
    stubMailApi(state())
    renderMailApp('/mail')
    const icons = await rail()
    await user.hover(icons)
    expect(pane()).toBeInTheDocument()
    // Still folded: the rail stays, the pane only over the list.
    expect(icons).toBeInTheDocument()
    await user.unhover(icons)
    expect(pane()).not.toBeInTheDocument()
  })

  it('peeks while focused, until Escape or the focus leaves', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(STORED, '"collapsed"')
    stubMailApi(state())
    renderMailApp('/mail')
    const icons = await rail()
    const sent = await within(icons).findByRole('link', { name: 'Sent' })
    act(() => {
      sent.focus()
    })
    expect(pane()).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(pane()).not.toBeInTheDocument()

    // Out and back: it peeks again; leaving closes it.
    await user.click(document.body)
    act(() => {
      sent.focus()
    })
    expect(pane()).toBeInTheDocument()
    await user.click(document.body)
    expect(pane()).not.toBeInTheDocument()
    expect(window.localStorage.getItem(STORED)).toBe('"collapsed"')
  })

  it("closes the peek on Escape, keeping the selection, but not a field's own Escape", async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(STORED, '"collapsed"')
    stubMailApi(state())
    renderMailApp('/mail')
    const icons = await rail()
    await user.click(await screen.findByRole('checkbox', { name: /^Select (?!all)/ }))
    const actions = await screen.findByRole('group', { name: 'Selected messages' })
    expect(actions).toHaveTextContent('1 selected')

    await user.hover(icons)
    expect(pane()).toBeInTheDocument()
    const search = screen.getByRole('searchbox')
    act(() => {
      search.focus()
    })
    await user.keyboard('{Escape}')
    expect(pane()).toBeInTheDocument()

    act(() => {
      search.blur()
    })
    await user.keyboard('{Escape}')
    expect(pane()).not.toBeInTheDocument()
    expect(actions).toHaveTextContent('1 selected')
  })

  it('stays under a menu opened from it, and closes once the pointer moves out after', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(STORED, '"collapsed"')
    stubMailApi(state())
    renderMailApp('/mail')
    const icons = await rail()
    await user.hover(icons)
    const peeked = screen.getByRole('navigation', { name: 'Accounts and folders' })
    act(() => {
      within(peeked).getByRole('treeitem', { name: /^Projects/ }).focus()
    })
    await user.keyboard('{Shift>}{F10}{/Shift}')
    expect(screen.getByRole('menu', { name: 'Actions for Projects' })).toBeInTheDocument()
    await user.unhover(icons)
    expect(pane()).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(pane()).toBeInTheDocument()
    await user.hover(screen.getByRole('searchbox'))
    expect(pane()).not.toBeInTheDocument()
    expect(within(icons).getByRole('button', { name: 'Open the folder pane' })).toHaveFocus()
  })

  it('closes the peek once a folder is picked from it', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem(STORED, '"collapsed"')
    stubMailApi(state())
    renderMailApp('/mail')
    const icons = await rail()
    act(() => {
      within(icons).getByRole('button', { name: 'Open the folder pane' }).focus()
    })
    const peeked = screen.getByRole('navigation', { name: 'Accounts and folders' })
    await user.click(within(peeked).getByRole('treeitem', { name: 'Sent' }))
    expect(await screen.findByRole('link', { name: /Sent one/ })).toBeInTheDocument()
    expect(pane()).not.toBeInTheDocument()
  })
})

describe('the folder pane choice', () => {
  it('folds on its own below xl only, and only on a wide screen', () => {
    expect(isCollapsed('auto', true, true)).toBe(false)
    expect(isCollapsed('auto', true, false)).toBe(true)
    expect(isCollapsed('collapsed', false, false)).toBe(false)
    expect(isCollapsed('expanded', true, false)).toBe(false)
  })

  it('goes back to auto when a toggle ends where auto would', () => {
    expect(toggled(false, true)).toBe('collapsed')
    expect(toggled(true, true)).toBe('auto')
    expect(toggled(true, false)).toBe('expanded')
    expect(toggled(false, false)).toBe('auto')
  })
})
