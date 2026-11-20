import { cleanup, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HOME, indexFolder, messageItem, queriesTo, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const READY = { enabled: true, state: 'ready', total: 1, indexed: 1, unread: 0 } as const

function state(index: FakeMailState['index']): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Projects', role: null, unread: 0, total: 1 },
        { path: 'Projects/2026', role: null, unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'From work' })],
      '1/Projects': [messageItem(WORK, 'Projects', 1, { subject: 'Plans' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {},
    searchResult: { messages: [messageItem(WORK, 'INBOX', 1, { subject: 'Found' })], next_cursor: null, failures: [] },
    index,
  }
}

const search = (query: object): string => `/mail?q=${encodeURIComponent(JSON.stringify(query))}`

/** The list has loaded, so the index status has had its answer too. */
async function listed(subject: string): Promise<void> {
  await screen.findByText(subject)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the indexing line', () => {
  it("shows how far the open folder's first sync has come", async () => {
    stubMailApi(
      state({ [WORK]: { folders: [indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'syncing', total: 8000, indexed: 1240, unread: 0 })] } }),
    )
    renderMailApp('/mail?account=1')
    expect(await screen.findByText('Indexing: 1,240 of 8,000 messages')).toBeInTheDocument()
  })

  it('says nothing for a folder ready in the index or off it', async () => {
    const fetch = stubMailApi(state({ [WORK]: { folders: [indexFolder('INBOX', { role: 'inbox', ...READY }), indexFolder('Projects')] } }))
    renderMailApp('/mail?account=1')
    await listed('From work')
    expect(queriesTo(fetch, '/api/v1/mail/index/accounts/1')).not.toHaveLength(0)
    expect(screen.queryByText(/^Indexing/)).toBeNull()

    cleanup()
    renderMailApp('/mail?account=1&folder=Projects')
    await listed('Plans')
    expect(screen.queryByText(/^Indexing/)).toBeNull()
  })

  it('counts the subfolders listed with the folder', async () => {
    stubMailApi(
      state({
        [WORK]: {
          folders: [
            indexFolder('Projects', { ...READY, total: 30, indexed: 30 }),
            indexFolder('Projects/2026', { enabled: true, state: 'pending', total: 50, indexed: 0 }),
          ],
        },
      }),
    )
    renderMailApp('/mail?account=1&folder=Projects')
    await listed('Plans')
    expect(screen.queryByText(/^Indexing/)).toBeNull()

    cleanup()
    renderMailApp('/mail?account=1&folder=Projects&sub=true')
    expect(await screen.findByText('Indexing: 0 of 50 messages')).toBeInTheDocument()
  })

  it('gives no total unless every waiting folder has one', async () => {
    stubMailApi(
      state({
        [WORK]: {
          folders: [
            indexFolder('Projects', { enabled: true, state: 'syncing', total: null, indexed: 1500 }),
            indexFolder('Projects/2026', { enabled: true, state: 'syncing', total: 1000, indexed: 0 }),
          ],
        },
      }),
    )
    renderMailApp('/mail?account=1&folder=Projects&sub=true')
    expect(await screen.findByText('Indexing: 1,500 messages so far')).toBeInTheDocument()
  })

  it('asks the server to count only the folders waiting', async () => {
    const fetch = stubMailApi(
      state({ [WORK]: { folders: [indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'syncing', total: 10, indexed: 1 })] } }),
    )
    renderMailApp('/mail?account=1')
    await screen.findByText('Indexing: 1 of 10 messages')
    const asked = queriesTo(fetch, '/api/v1/mail/index/accounts/1')
    expect(asked).not.toHaveLength(0)
    expect(asked.every((params) => params.get('totals') === 'waiting')).toBe(true)
  })

  it('reads a Gmail label from All Mail', async () => {
    stubMailApi(
      state({
        [WORK]: {
          gmail: true,
          folders: [
            indexFolder('INBOX', { role: 'inbox', indexed_as: 'label', enabled: true, state: 'syncing', total: 40 }),
            indexFolder('[Gmail]/All Mail', { role: 'all', enabled: true, state: 'syncing', total: 2000, indexed: 500 }),
          ],
        },
      }),
    )
    renderMailApp('/mail?account=1')
    expect(await screen.findByText('Indexing: 500 of 2,000 messages')).toBeInTheDocument()
  })

  it('shows an all-accounts search waiting on any account, never a search of the text', async () => {
    const index: FakeMailState['index'] = {
      [WORK]: { folders: [indexFolder('INBOX', { role: 'inbox', ...READY })] },
      [HOME]: { folders: [indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'syncing', total: 300, indexed: 120 })] },
    }
    stubMailApi(state(index))
    renderMailApp(search({}))
    expect(await screen.findByText('Indexing: 120 of 300 messages')).toBeInTheDocument()

    cleanup()
    renderMailApp(search({ account: String(WORK) }))
    await listed('Found')
    expect(screen.queryByText(/^Indexing/)).toBeNull()

    cleanup()
    renderMailApp(search({ text: 'home' }))
    await listed('Found')
    expect(screen.queryByText(/^Indexing/)).toBeNull()
  })
})
