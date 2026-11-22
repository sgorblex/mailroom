import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { FakeNotification, stubNotification, stubRefusingNotification } from '@/test/fake-notification.ts'
import { bodiesTo, HOME, indexFolder, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { NotificationSettings } from './notification-settings.tsx'

const ALICE: User = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {},
    messages: {},
    views: {},
    index: {
      [WORK]: {
        folders: [
          indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'ready' }),
          indexFolder('Archive', { role: 'archive', enabled: true, state: 'ready' }),
          indexFolder('Junk', { role: 'junk' }),
          // A server's own \All folder, a virtual one: its new mail is its own.
          indexFolder('All', { role: 'all', enabled: true, state: 'ready' }),
        ],
      },
      [HOME]: {
        gmail: true,
        folders: [
          indexFolder('INBOX', { role: 'inbox', indexed_as: 'label', enabled: true }),
          indexFolder('[Gmail]/All Mail', { role: 'all', enabled: true, state: 'ready' }),
          indexFolder('Work', { indexed_as: 'label', enabled: true }),
          indexFolder('[Gmail]/Spam', { role: 'junk', indexed_as: 'live' }),
        ],
      },
    },
    ...fields,
  }
}

const folderList = (name: string): Promise<HTMLElement> => screen.findByRole('list', { name: `Folders of ${name} that notify` })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('NotificationSettings', () => {
  it('asks for the permission only from its button', async () => {
    stubNotification('default', 'granted')
    stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)

    const allow = await screen.findByRole('button', { name: 'Allow desktop notifications' })
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
    await userEvent.click(allow)
    expect(FakeNotification.requestPermission).toHaveBeenCalledOnce()
    expect(await screen.findByText("This browser shows Mailroom's notifications.")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow desktop notifications' })).not.toBeInTheDocument()
  })

  it.each([
    ['denied', "This browser blocks Mailroom's notifications: allow them in its settings for this site."],
    ['granted', "This browser shows Mailroom's notifications."],
  ] as const)('says when the browser has %s them', async (permission, text) => {
    stubNotification(permission)
    stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)
    expect(await screen.findByText(text)).toBeInTheDocument()
  })

  it('says when the browser cannot show them', async () => {
    stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)
    expect(await screen.findByText('This browser cannot show notifications.')).toBeInTheDocument()
  })

  it('says the browser cannot show them when it refuses to construct one', async () => {
    stubRefusingNotification('default')
    stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)
    expect(await screen.findByText('This browser cannot show notifications.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow desktop notifications' })).not.toBeInTheDocument()
  })

  it("offers each account's indexed folders, INBOX checked until the user chooses", async () => {
    stubNotification('granted')
    stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)

    const work = await folderList('work')
    expect(within(work).getAllByRole('checkbox').map((box) => box.closest('li')?.textContent)).toEqual(['INBOX', 'Archive', 'All'])
    expect(within(work).getByRole('checkbox', { name: 'INBOX' })).toBeChecked()
    expect(within(work).getByRole('checkbox', { name: 'Archive' })).not.toBeChecked()
    // On Gmail only INBOX's new mail is told apart, read from All Mail.
    const home = await folderList('home')
    expect(within(home).getAllByRole('checkbox').map((box) => box.closest('li')?.textContent)).toEqual(['INBOX'])
  })

  it("saves an account's folders all at once", async () => {
    stubNotification('granted')
    const fetchMock = stubMailApi(state())
    renderSignedIn(<NotificationSettings />, ALICE)

    const work = await folderList('work')
    await userEvent.click(within(work).getByRole('checkbox', { name: 'Archive' }))
    expect(await within(work).findByRole('checkbox', { name: 'Archive' })).toBeChecked()
    await userEvent.click(within(work).getByRole('checkbox', { name: 'INBOX' }))
    expect(await within(work).findByRole('checkbox', { name: 'INBOX' })).not.toBeChecked()
    expect(bodiesTo(fetchMock, `/api/v1/mail/notifications/accounts/${String(WORK)}`)).toEqual([
      { folders: ['INBOX', 'Archive'] },
      { folders: ['Archive'] },
    ])
  })
})
