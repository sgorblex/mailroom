import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_APPEARANCE, type UserJson } from '@/api/auth.ts'
import { AuthProvider } from '@/auth/auth-provider.tsx'
import { renderSignedIn } from '@/test/signed-in.tsx'
import { LookSync } from '@/theme/look-sync.tsx'

import { AppearanceSettings } from './appearance-settings.tsx'

const PATH = '/api/v1/auth/me/appearance'
const ALICE = {
  username: 'alice',
  is_admin: false,
  time_zone: null,
  mark_read_subfolders: true,
  index_max_age_days: null,
  conversations: true,
  undo_send_seconds: 10,
  appearance: { ...DEFAULT_APPEARANCE, theme: 'light' },
} satisfies UserJson

function stubApi(status: number): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return Promise.resolve(new Response('{"detail":"Server error"}', { status }))
    }
    const change = JSON.parse(init?.body as string) as object
    return Promise.resolve(new Response(JSON.stringify({ ...ALICE, appearance: { ...ALICE.appearance, ...change } }), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const root = document.documentElement

beforeEach(() => {
  root.className = ''
  delete root.dataset['accent']
  delete root.dataset['textSize']
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AppearanceSettings', () => {
  it('starts from the saved look', () => {
    stubApi(200)
    renderSignedIn(<AppearanceSettings />, { ...ALICE, appearance: { ...DEFAULT_APPEARANCE, theme: 'dark', accent: 'rose', text_size: 'large' } })
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Rose' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Large' })).toBeChecked()
  })

  it('names every accent', () => {
    stubApi(200)
    renderSignedIn(<AppearanceSettings />, ALICE)
    const accents = screen.getByRole('group', { name: 'Accent colour' })
    expect(accents).toBeInTheDocument()
    for (const name of ['Teal', 'Blue', 'Violet', 'Green', 'Terracotta', 'Rose']) {
      expect(screen.getByRole('radio', { name })).toBeInTheDocument()
    }
  })

  it('shows a choice at once and saves only it', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<AppearanceSettings />, ALICE)

    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(root).toHaveClass('dark')
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenCalledWith(PATH, expect.objectContaining({ method: 'PUT', body: JSON.stringify({ theme: 'dark' }) }))

    await userEvent.click(screen.getByRole('radio', { name: 'Violet' }))
    expect(root.dataset['accent']).toBe('violet')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ accent: 'violet' }) }))

    await userEvent.click(screen.getByRole('radio', { name: 'Small' }))
    expect(root.dataset['textSize']).toBe('small')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ text_size: 'small' }) }))
  })

  it('saves the density of the mail lists', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<AppearanceSettings />, ALICE)
    const density = screen.getByRole('group', { name: 'Density' })
    expect(within(density).getByRole('radio', { name: 'Comfortable' })).toBeChecked()
    await userEvent.click(within(density).getByRole('radio', { name: /^Compact/ }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ density: 'compact' }) }))
    expect(within(density).getByRole('radio', { name: /^Compact/ })).toBeChecked()
  })

  it('saves where a message opens, and says a narrow screen always opens it in full', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<AppearanceSettings />, ALICE)
    const pane = screen.getByRole('group', { name: 'Reading pane' })
    expect(pane).toHaveAccessibleDescription(
      'On a narrow screen (a phone, or a window under 1,024 px) a message always opens in place of the list.',
    )
    expect(screen.getByRole('radio', { name: 'On the right of the list' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Below the list' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ reading_pane: 'below' }) }))
    await userEvent.click(screen.getByRole('radio', { name: /^Off/ }))
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ reading_pane: 'off' }) }))
    expect(screen.getByRole('radio', { name: /^Off/ })).toBeChecked()
  })

  it('saves what swiping a row left or right does, saying it is on a touch screen', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<AppearanceSettings />, ALICE)
    expect(screen.getByRole('group', { name: 'Swipe actions' })).toHaveAccessibleDescription(/on a touch screen/)
    const left = screen.getByRole('combobox', { name: 'Swipe left' })
    const right = screen.getByRole('combobox', { name: 'Swipe right' })
    expect(left).toHaveValue('trash')
    expect(right).toHaveValue('archive')

    await userEvent.selectOptions(left, 'Nothing')
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ swipe_left: 'none' }) }))
    await userEvent.selectOptions(right, 'Star or unstar')
    expect(fetchMock).toHaveBeenLastCalledWith(PATH, expect.objectContaining({ body: JSON.stringify({ swipe_right: 'star' }) }))
    expect(left).toHaveValue('none')
    expect(right).toHaveValue('star')
  })

  it('puts the saved look back when the choice could not be saved', async () => {
    stubApi(500)
    renderSignedIn(<AppearanceSettings />, ALICE)
    await userEvent.click(screen.getByRole('radio', { name: 'Green' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Teal' })).toBeChecked()
    expect(root.dataset['accent']).toBe('default')
  })

  it('keeps the newest choice when an earlier one fails after it was saved', async () => {
    const answers: ((response: Response) => void)[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === PATH
          ? new Promise<Response>((resolve) => answers.push(resolve))
          : Promise.resolve(new Response(JSON.stringify({ user: ALICE, features: ['mail'] }), { status: 200 })),
      ),
    )
    render(
      <AuthProvider>
        <LookSync />
        <AppearanceSettings />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked())

    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    await userEvent.click(screen.getByRole('radio', { name: 'Violet' }))
    expect(answers).toHaveLength(2)

    // The Violet save answers first, from a server that has not had the Dark one: while Dark is
    // still being saved, the section and the page keep both choices.
    const violet = { ...ALICE, appearance: { ...ALICE.appearance, accent: 'violet' } }
    act(() => answers[1]?.(new Response(JSON.stringify(violet), { status: 200 })))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
    expect(root).toHaveClass('dark')

    // Then the Dark save fails: what the server holds (light, violet) shows, not the look from
    // before both choices; and the failure of a replaced choice is not reported.
    act(() => answers[0]?.(new Response('{"detail":"Server error"}', { status: 500 })))
    // The radios change on the commit, the page in the effect after it: wait for both.
    await waitFor(() => {
      expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
      expect(root).not.toHaveClass('dark')
    })
    expect(screen.getByRole('radio', { name: 'Violet' })).toBeChecked()
    expect(root.dataset['accent']).toBe('violet')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
