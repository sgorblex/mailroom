import { act, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeBroadcastChannel, stubBroadcastChannel } from '@/test/fake-broadcast-channel.ts'
import { FakeEventSource, stubEventSource } from '@/test/fake-event-source.ts'
import { FakeNotification, stubNotification, stubRefusingNotification } from '@/test/fake-notification.ts'
import { HOME, messageItem, messageView, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { notificationAccess } from './notification-choice.ts'

const fresh = [messageItem(WORK, 'INBOX', 12), messageItem(WORK, 'INBOX', 11)]
const archived = messageItem(WORK, 'Archive', 3, { subject: 'Filed' })

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 2, total: 2 },
        { path: 'Archive', role: 'archive', unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': fresh, '1/Archive': [archived], '2/INBOX': [] },
    views: Object.fromEntries([...fresh, archived].map((item) => [item.ref, messageView(item)])),
    ...fields,
  }
}

const newMail = (folder = 'INBOX', count = 2): Record<string, unknown> => ({
  account: WORK,
  folder,
  count,
  messages: fresh.map((item) => ({ ref: item.ref, sender: item.from.name, subject: item.subject })),
})

/** The mail screen at `path`, its stream open. */
async function streaming(path: string): Promise<FakeEventSource> {
  renderMailApp(path)
  await screen.findByRole('list', { name: 'Messages' })
  const source = await waitFor(() => {
    const current = FakeEventSource.current()
    if (current?.url.includes('folder=') !== true) {
      throw new Error('no stream open on the folder')
    }
    return current
  })
  act(() => {
    source.open()
  })
  return source
}

function send(source: FakeEventSource, data: Record<string, unknown>): void {
  act(() => {
    source.send('new_mail', data)
  })
}

const shown = (): { title: string; body: string | undefined; tag: string | undefined }[] =>
  FakeNotification.instances.map(({ title, body, tag }) => ({ title, body, tag }))

let focused = false

beforeEach(() => {
  focused = false
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focused)
  stubEventSource()
  stubBroadcastChannel()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('desktop notifications', () => {
  it("shows each new message's sender and subject, tagged by its ref", async () => {
    stubNotification('granted')
    stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=Archive')

    send(source, newMail())
    await waitFor(() => {
      expect(shown()).toEqual([
        { title: 'Sender 12', body: 'Message 12', tag: `mailroom:${fresh[0]?.ref ?? ''}` },
        { title: 'Sender 11', body: 'Message 11', tag: `mailroom:${fresh[1]?.ref ?? ''}` },
      ])
    })
  })

  it('sums up past the messages the event names, and fills in an empty sender or subject', async () => {
    stubNotification('granted')
    stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=Archive')

    send(source, { account: WORK, folder: 'INBOX', count: 7, messages: [{ ref: fresh[0]?.ref, sender: '', subject: '' }] })
    await waitFor(() => {
      expect(shown().map(({ title, body }) => [title, body])).toEqual([
        ['(unknown sender)', '(no subject)'],
        ['7 new messages', undefined],
      ])
    })
  })

  it('stays quiet for the folder the focused tab shows, not once the tab loses focus', async () => {
    stubNotification('granted')
    const fetchMock = stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=INBOX')

    focused = true
    send(source, newMail())
    send(source, newMail('Archive'))
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/notifications')).toHaveLength(1)
    })
    expect(shown()).toEqual([])

    focused = false
    send(source, newMail())
    await waitFor(() => {
      expect(shown()).toHaveLength(2)
    })
  })

  it('stays quiet for the folder another focused tab announces, not once that tab blurs', async () => {
    stubNotification('granted')
    const fetchMock = stubMailApi(state())
    const other = new FakeBroadcastChannel('mailroom-viewing')
    const source = await streaming('/mail?account=1&folder=Archive')

    act(() => {
      other.postMessage({ tab: 'other', viewing: { account: WORK, folder: 'INBOX' } })
    })
    send(source, newMail())
    send(source, newMail('Archive'))
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/notifications')).toHaveLength(1)
    })
    expect(shown()).toEqual([])

    act(() => {
      other.postMessage({ tab: 'other', viewing: null })
    })
    send(source, newMail())
    await waitFor(() => {
      expect(shown()).toHaveLength(2)
    })
  })

  it('announces the folder it shows while focused, and null once it blurs', async () => {
    stubNotification('granted')
    stubMailApi(state())
    const other = new FakeBroadcastChannel('mailroom-viewing')
    await streaming('/mail?account=1&folder=INBOX')

    focused = true
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    focused = false
    act(() => {
      window.dispatchEvent(new Event('blur'))
    })
    expect(other.received.map((data) => (data as { viewing: unknown }).viewing)).toEqual([
      { account: WORK, folder: 'INBOX' },
      null,
    ])
  })

  it('keeps to the focused tab alone without BroadcastChannel', async () => {
    vi.stubGlobal('BroadcastChannel', undefined)
    stubNotification('granted')
    const fetchMock = stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=INBOX')

    focused = true
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    send(source, newMail())
    send(source, newMail('Archive'))
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/notifications')).toHaveLength(1)
    })
    expect(shown()).toEqual([])

    focused = false
    send(source, newMail())
    await waitFor(() => {
      expect(shown()).toHaveLength(2)
    })
  })

  it("follows the user's folder choice", async () => {
    stubNotification('granted')
    stubMailApi(state({ notifications: { [WORK]: ['Archive'] } }))
    const source = await streaming('/mail?account=1&folder=INBOX')

    send(source, newMail())
    send(source, newMail('Archive'))
    await waitFor(() => {
      expect(shown()).toHaveLength(2)
    })
    send(source, { ...newMail(), account: HOME, messages: [{ ref: 'home-ref', sender: 'Someone', subject: 'Hi' }], count: 1 })
    await waitFor(() => {
      expect(shown().at(-1)?.title).toBe('Someone')
    })
  })

  it('shows nothing, nor reads the choice, without permission', async () => {
    stubNotification('default')
    const fetchMock = stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=Archive')

    send(source, newMail())
    await act(() => Promise.resolve())
    expect(shown()).toEqual([])
    expect(queriesTo(fetchMock, '/api/v1/mail/notifications')).toHaveLength(0)
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled()
  })

  it('shows nothing where the browser refuses to construct one, and says so from then on', async () => {
    stubRefusingNotification('granted')
    const fetchMock = stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=Archive')

    send(source, newMail())
    await waitFor(() => {
      expect(notificationAccess()).toBe('unsupported')
    })
    expect(shown()).toEqual([])
    const reads = queriesTo(fetchMock, '/api/v1/mail/notifications').length
    send(source, newMail())
    await act(() => Promise.resolve())
    expect(queriesTo(fetchMock, '/api/v1/mail/notifications')).toHaveLength(reads)
  })

  it('focuses the tab and opens the message on a click', async () => {
    stubNotification('granted')
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => undefined)
    stubMailApi(state())
    const source = await streaming('/mail?account=1&folder=Archive')

    send(source, newMail())
    await waitFor(() => {
      expect(shown()).toHaveLength(2)
    })
    act(() => {
      FakeNotification.instances[1]?.click()
    })
    expect(focus).toHaveBeenCalled()
    expect(FakeNotification.instances[1]?.closed).toBe(true)
    await waitFor(() => {
      expect(screen.getByRole('article')).toHaveTextContent('Body of Message 11')
    })
  })
})
