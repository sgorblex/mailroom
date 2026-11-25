import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { expectNoAxeViolations } from '@/test/axe.ts'

import { InstanceSettingsForm } from './instance-settings-form.tsx'

const PATH = '/api/v1/admin/settings'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API; a PUT keeps what it is sent, or is refused with `refusal`. */
function stubApi(refusal?: Response, held: Record<string, unknown> = {}): ReturnType<typeof vi.fn> {
  let settings: Record<string, unknown> = {
    audit_retention_days: 90,
    session_ip: 'partial',
    external_sign_in_only: false,
    external_sign_in: 'available',
    password_sign_in_forced: false,
    ...held,
  }
  const fetchMock = vi.fn((_path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      if (refusal !== undefined) {
        return Promise.resolve(refusal)
      }
      settings = { ...settings, ...(JSON.parse(init.body as string) as Record<string, unknown>) }
    }
    return Promise.resolve(json(200, settings))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('InstanceSettingsForm', () => {
  it('shows the settings and saves a change', async () => {
    const fetchMock = stubApi()
    render(<InstanceSettingsForm />)
    const days = await screen.findByLabelText('Days the activity is kept')
    expect(days).toHaveValue(90)
    const ip = screen.getByLabelText('IP address of a signed-in session')
    expect(ip).toHaveValue('partial')
    await expectNoAxeViolations()

    await userEvent.clear(days)
    await userEvent.type(days, '30')
    await userEvent.selectOptions(ip, 'Not recorded')
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock).toHaveBeenLastCalledWith(
      PATH,
      expect.objectContaining({
        method: 'PUT',
        body: '{"audit_retention_days":30,"session_ip":"none"}',
      }),
    )

    await userEvent.selectOptions(ip, 'Full')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('sends only what changed since it loaded or was saved', async () => {
    const fetchMock = stubApi()
    render(<InstanceSettingsForm />)
    const ip = await screen.findByLabelText('IP address of a signed-in session')
    // Another admin changes the other setting meanwhile.
    await fetch(PATH, { method: 'PUT', body: '{"audit_retention_days":365}' })
    for (const [choice, body] of [
      ['Full', '{"session_ip":"full"}'],
      ['Not recorded', '{"session_ip":"none"}'],
    ] as const) {
      await userEvent.selectOptions(ip, choice)
      await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
      expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
      expect(fetchMock.mock.lastCall?.[1]).toHaveProperty('body', body)
      expect(screen.getByLabelText('Days the activity is kept')).toHaveValue(365)
    }
  })

  it('does not send a retention out of bounds', async () => {
    const fetchMock = stubApi()
    render(<InstanceSettingsForm />)
    const days = await screen.findByLabelText('Days the activity is kept')
    await userEvent.clear(days)
    await userEvent.type(days, '3651')
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(days).toBeInvalid()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('says a save was refused and keeps what was typed', async () => {
    stubApi(json(403, { detail: 'Admins only' }))
    render(<InstanceSettingsForm />)
    const days = await screen.findByLabelText('Days the activity is kept')
    await userEvent.clear(days)
    await userEvent.type(days, '30')
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Admins only')
    expect(days).toHaveValue(30)
  })

  it('offers to try again when the settings do not load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json(503, { detail: 'Database unavailable' }))),
    )
    render(<InstanceSettingsForm />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable')
    stubApi()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByLabelText('Days the activity is kept')).toHaveValue(90)
  })

  it('turns external sign-in only on, and says so to the page', async () => {
    const fetchMock = stubApi()
    const onSaved = vi.fn()
    render(<InstanceSettingsForm onSaved={onSaved} />)
    await userEvent.click(await screen.findByLabelText('External sign-in only'))
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(fetchMock.mock.lastCall?.[1]).toHaveProperty(
      'body',
      '{"external_sign_in_only":true}',
    )
    expect(onSaved).toHaveBeenCalledOnce()
    expect(screen.getByLabelText('External sign-in only')).toBeChecked()
  })

  it('does not offer external sign-in only without a provider', async () => {
    stubApi(undefined, { external_sign_in: 'not_configured' })
    render(<InstanceSettingsForm />)
    await screen.findByLabelText('Days the activity is kept')
    expect(screen.queryByLabelText('External sign-in only')).not.toBeInTheDocument()
  })

  it('asks the admin to link their own sign-in first', async () => {
    stubApi(undefined, { external_sign_in: 'not_linked' })
    render(<InstanceSettingsForm />)
    expect(await screen.findByLabelText('External sign-in only')).toBeDisabled()
    expect(screen.getByText(/Link your own sign-in on your profile first/)).toBeInTheDocument()
    await expectNoAxeViolations()
  })

  it.each([
    [{ external_sign_in: 'not_configured' }, /no identity provider is configured/],
    [{ password_sign_in_forced: true }, /the server's environment keeps passwords working/],
  ])('says when the setting has no effect, and can still turn it off (%o)', async (held, said) => {
    stubApi(undefined, { external_sign_in_only: true, ...held })
    render(<InstanceSettingsForm />)
    expect(await screen.findByLabelText('External sign-in only')).toBeEnabled()
    expect(screen.getByText(said)).toBeInTheDocument()
  })

  it('words the refusal of an admin whose sign-in was unlinked meanwhile', async () => {
    const detail = { code: 'external_sign_in_not_linked', message: 'Refused.' }
    stubApi(json(409, { detail }))
    render(<InstanceSettingsForm />)
    await userEvent.click(await screen.findByLabelText('External sign-in only'))
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Link your own sign-in')
  })
})
