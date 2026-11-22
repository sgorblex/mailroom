import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { UndoSendForm } from './undo-send-form.tsx'

const PATH = '/api/v1/auth/me/undo-send'
const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

function stubApi(status: number): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return Promise.resolve(new Response('{"detail":"Server error"}', { status }))
    }
    const { undo_send_seconds } = JSON.parse(init?.body as string) as { undo_send_seconds: number }
    return Promise.resolve(new Response(JSON.stringify({ ...ALICE, undo_send_seconds }), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UndoSendForm', () => {
  it('starts from the saved delay and saves the one picked, off included', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<UndoSendForm />, ALICE)
    const choice = screen.getByRole('combobox', { name: 'Time to undo a send' })
    expect(choice).toHaveValue('10')
    expect(choice).toHaveAccessibleDescription(/Undo/)

    await userEvent.selectOptions(choice, 'Off: send at once')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenCalledWith(PATH, expect.objectContaining({ method: 'PUT', body: JSON.stringify({ undo_send_seconds: 0 }) }))
  })

  it('keeps a delay set elsewhere among the choices', () => {
    stubApi(200)
    renderSignedIn(<UndoSendForm />, { ...ALICE, undo_send_seconds: 7 })
    expect(screen.getByRole('combobox', { name: 'Time to undo a send' })).toHaveValue('7')
    expect(screen.getByRole('option', { name: '7 seconds' })).toBeInTheDocument()
  })

  it('says when the delay could not be saved', async () => {
    stubApi(500)
    renderSignedIn(<UndoSendForm />, ALICE)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
