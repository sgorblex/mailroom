import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { TimeZoneForm } from './time-zone-form.tsx'

const ZONE_PATH = '/api/v1/auth/me/time-zone'

function stubApi(status: number): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (status !== 200) {
      return Promise.resolve(new Response('{"detail":"Unknown time zone"}', { status }))
    }
    const { time_zone } = JSON.parse(init?.body as string) as { time_zone: string }
    const user = { username: 'alice', is_admin: false, time_zone, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
    return Promise.resolve(new Response(JSON.stringify(user), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TimeZoneForm', () => {
  it('starts from the saved zone and saves the one picked', async () => {
    const fetchMock = stubApi(200)
    renderSignedIn(<TimeZoneForm />, { username: 'alice', is_admin: false, time_zone: 'Europe/Rome', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 })
    const picker = screen.getByRole('combobox', { name: 'Time zone' })
    expect(picker).toHaveValue('Europe/Rome')
    expect(picker).toHaveAccessibleDescription('Mailroom writes your dates in this time zone.')
    expect(screen.getByRole('option', { name: 'America/New York' })).toHaveValue('America/New_York')

    await userEvent.selectOptions(picker, 'Asia/Tokyo')
    await userEvent.click(screen.getByRole('button', { name: 'Save time zone' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Time zone saved.')
    expect(fetchMock).toHaveBeenCalledWith(
      ZONE_PATH,
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ time_zone: 'Asia/Tokyo' }) }),
    )
  })

  it("starts from the browser's zone when none is saved", () => {
    stubApi(200)
    renderSignedIn(<TimeZoneForm />, { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 })
    expect(screen.getByRole('combobox', { name: 'Time zone' })).toHaveValue(Intl.DateTimeFormat().resolvedOptions().timeZone)
  })

  it('says when the zone could not be saved', async () => {
    stubApi(500)
    renderSignedIn(<TimeZoneForm />, { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 })
    await userEvent.click(screen.getByRole('button', { name: 'Save time zone' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
