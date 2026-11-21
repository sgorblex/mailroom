import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { MarkReadForm } from './mark-read-form.tsx'

const PATH = '/api/v1/auth/me/mark-read-subfolders'
const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true }

function stubApi(status: number): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return Promise.resolve(new Response('{"detail":"Server error"}', { status }))
    }
    const { mark_read_subfolders } = JSON.parse(init?.body as string) as { mark_read_subfolders: boolean }
    return Promise.resolve(new Response(JSON.stringify({ ...ALICE, mark_read_subfolders }), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('MarkReadForm', () => {
  it('starts from the saved choice and saves the one picked', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<MarkReadForm />, ALICE)
    expect(screen.getByRole('radio', { name: 'The folder and its subfolders' })).toBeChecked()
    expect(screen.getByRole('group', { name: 'Mark all as read' })).toHaveAccessibleDescription(/what “Mark all as read” in its menu does first/)

    await userEvent.click(screen.getByRole('radio', { name: 'The folder only' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenCalledWith(
      PATH,
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ mark_read_subfolders: false }) }),
    )
  })

  it('starts from the folder only when that is saved', () => {
    stubApi(200)
    renderSignedIn(<MarkReadForm />, { ...ALICE, mark_read_subfolders: false })
    expect(screen.getByRole('radio', { name: 'The folder only' })).toBeChecked()
  })

  it('says when the choice could not be saved', async () => {
    stubApi(500)
    renderSignedIn(<MarkReadForm />, ALICE)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
