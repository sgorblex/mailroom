import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

// jsdom lays nothing out: these pin the classes the browser's layout depends on.
describe('the mail screen on a wide screen', () => {
  it('takes the height left under the header, not its content’s, so each pane scrolls on its own', async () => {
    stubMailApi({
      accounts: [WORK],
      folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }] },
      messages: { '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'Hello' })] },
      views: {},
      searchResult: { messages: [], next_cursor: null, failures: [] },
    })
    renderMailApp('/mail?account=1')
    await screen.findByText('Hello')
    const main = screen.getByRole('main')
    // A 0 px basis: flex-1's 0 % in a column as high as its content is read as the content's height.
    expect(main).toHaveClass('flex-1', 'lg:basis-0', 'lg:min-h-0')
    expect(main).not.toHaveClass('lg:h-0')
    for (const name of ['Messages', 'Message']) {
      expect(screen.getByRole('region', { name })).toHaveClass('lg:overflow-y-auto')
    }
  })
})
