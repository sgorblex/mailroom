import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { UsersScreen } from './users-screen.tsx'

const ROOT = { username: 'root', is_admin: true }
const CREATED = '2026-10-17T10:00:00Z'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API holding root (id 1) and alice (id 2); `refuseRemoval` answers alice's removal. */
function stubApi(refuseRemoval?: { status: number; code: string }): ReturnType<typeof vi.fn> {
  let users = [
    { id: 2, username: 'alice', is_admin: false, created_at: CREATED },
    { id: 1, username: 'root', is_admin: true, created_at: CREATED },
  ]
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (path === '/api/v1/users' && method === 'GET') {
      return Promise.resolve(json(200, users))
    }
    if (path === '/api/v1/users' && method === 'POST') {
      const { username, is_admin } = JSON.parse(init?.body as string) as {
        username: string
        is_admin: boolean
      }
      if (users.some((user) => user.username === username)) {
        const detail = { code: 'username_taken', message: `The username '${username}' is taken.` }
        return Promise.resolve(json(400, { detail }))
      }
      const created = { id: 3, username, is_admin, created_at: CREATED }
      users = [...users, created].sort((a, b) => a.username.localeCompare(b.username))
      return Promise.resolve(json(201, created))
    }
    if (path === '/api/v1/users/2' && method === 'DELETE') {
      if (refuseRemoval !== undefined) {
        const detail = { code: refuseRemoval.code, message: 'Refused.' }
        return Promise.resolve(json(refuseRemoval.status, { detail }))
      }
      users = users.filter((user) => user.id !== 2)
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    if (path === '/api/v1/users/2/password' && method === 'POST') {
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    return Promise.resolve(json(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UsersScreen', () => {
  it('tells a user who is not an admin that only admins manage users', () => {
    const fetchMock = stubApi()
    renderSignedIn(<UsersScreen />, { username: 'alice', is_admin: false })
    expect(screen.getByRole('alert')).toHaveTextContent('Only admins manage users.')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('lists users, adds one and refuses a taken name', async () => {
    stubApi()
    renderSignedIn(<UsersScreen />, ROOT)

    const list = await screen.findByRole('list', { name: 'Users' })
    expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      expect.stringContaining('alice'),
      expect.stringContaining('root'),
    ])
    // Their own row offers nothing: their password changes on their profile.
    expect(within(list).queryByRole('button', { name: 'Remove root' })).not.toBeInTheDocument()
    expect(
      within(list).queryByRole('button', { name: 'Reset the password of root' }),
    ).not.toBeInTheDocument()

    const form = screen.getByRole('form', { name: 'Add a user' })
    await userEvent.type(within(form).getByLabelText('Username'), 'alice')
    await userEvent.type(within(form).getByLabelText('Password'), 'a long enough password')
    await userEvent.click(within(form).getByRole('button', { name: 'Add user' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('That username is taken.')

    await userEvent.clear(within(form).getByLabelText('Username'))
    await userEvent.type(within(form).getByLabelText('Username'), 'bob')
    await userEvent.click(within(form).getByLabelText('Admin: may manage users'))
    await userEvent.click(within(form).getByRole('button', { name: 'Add user' }))
    expect(await within(list).findByText('bob')).toBeInTheDocument()
    expect(within(form).getByLabelText('Username')).toHaveValue('')
  })

  it('offers to try again when the list does not load', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json(503, { detail: 'Database unavailable' }))),
    )
    renderSignedIn(<UsersScreen />, ROOT)
    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable')
    stubApi()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('list', { name: 'Users' })).toBeInTheDocument()
  })

  it('moves focus into a row action and back to its button', async () => {
    stubApi()
    renderSignedIn(<UsersScreen />, ROOT)

    const reset = await screen.findByRole('button', { name: 'Reset the password of alice' })
    await userEvent.click(reset)
    expect(screen.getByLabelText('New password for alice')).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Reset the password of alice' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Remove alice' }))
    expect(screen.getByRole('button', { name: 'Remove for good' })).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Remove alice' })).toHaveFocus()
  })

  it('resets a password and removes a user after confirming', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<UsersScreen />, ROOT)

    await userEvent.click(
      await screen.findByRole('button', { name: 'Reset the password of alice' }),
    )
    await userEvent.type(screen.getByLabelText('New password for alice'), 'a brand new passphrase')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Password reset')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/users/2/password',
      expect.objectContaining({ body: '{"password":"a brand new passphrase"}' }),
    )

    await userEvent.click(screen.getByRole('button', { name: 'Remove alice' }))
    expect(screen.getByText('Remove alice and all their data?')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    await vi.waitFor(() => {
      expect(screen.queryByText('alice')).not.toBeInTheDocument()
    })
  })

  it('words a removal refused while their accounts are being saved', async () => {
    stubApi({ status: 503, code: 'user_accounts_busy' })
    renderSignedIn(<UsersScreen />, ROOT)

    await userEvent.click(await screen.findByRole('button', { name: 'Remove alice' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    expect(
      await screen.findByText('Their accounts are being saved. Try again.'),
    ).toBeInTheDocument()
    expect(screen.getByText('alice')).toBeInTheDocument()
  })
})
