import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { ConversationsForm } from './conversations-form.tsx'

const PATH = '/api/v1/auth/me/conversations'
const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

function stubApi(status: number): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return Promise.resolve(new Response('{"detail":"Server error"}', { status }))
    }
    const { conversations } = JSON.parse(init?.body as string) as { conversations: boolean }
    return Promise.resolve(new Response(JSON.stringify({ ...ALICE, conversations }), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConversationsForm', () => {
  it('starts from the saved choice and saves the one picked', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<ConversationsForm />, ALICE)
    const box = screen.getByRole('checkbox', { name: 'Group messages by conversation' })
    expect(box).toBeChecked()
    expect(box).toHaveAccessibleDescription(/Each conversation is then one row/)

    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenCalledWith(PATH, expect.objectContaining({ method: 'PUT', body: JSON.stringify({ conversations: false }) }))
  })

  it('starts unticked when they are not grouped', () => {
    stubApi(200)
    renderSignedIn(<ConversationsForm />, { ...ALICE, conversations: false })
    expect(screen.getByRole('checkbox', { name: 'Group messages by conversation' })).not.toBeChecked()
  })

  it('says when the choice could not be saved', async () => {
    stubApi(500)
    renderSignedIn(<ConversationsForm />, ALICE)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
