import { type ComponentProps } from 'react'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link as RouterLink } from '@tanstack/react-router'
import { expect, it, vi } from 'vitest'

import { HOME, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const drawn = vi.hoisted(() => ({ rows: 0 }))

vi.mock('@tanstack/react-router', async (original) => {
  const router = await original<{ Link: typeof RouterLink }>()
  const { Link } = router
  return {
    ...router,
    Link: (props: ComponentProps<typeof RouterLink> & { 'data-ref'?: string }) => {
      if (props['data-ref'] !== undefined) {
        drawn.rows += 1
      }
      return <Link {...props} />
    },
  }
})

// Built without React Compiler, every row's contents were drawn again.
it("draws again only the rows a newly opened message changes", async () => {
  const inbox = Array.from({ length: 20 }, (_, index) => messageItem(WORK, 'INBOX', 100 - index))
  stubMailApi({
    accounts: [WORK, HOME],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 20 }], [HOME]: [] },
    messages: { '1/INBOX': inbox },
    views: Object.fromEntries(inbox.map((message) => [message.ref, messageView(message)])),
  })
  renderMailApp('/mail')
  const open = async (subject: string): Promise<void> => {
    await userEvent.click(within(screen.getByRole('list', { name: 'Messages' })).getByRole('link', { name: new RegExp(subject) }))
    await screen.findByRole('heading', { name: subject })
  }
  await screen.findByRole('link', { name: /Message 99/ })
  await open('Message 99')
  await screen.findByText('Body of Message 99')

  drawn.rows = 0
  await open('Message 98')
  await screen.findByText('Body of Message 98')
  expect(drawn.rows).toBeGreaterThan(0)
  expect(drawn.rows).toBeLessThan(inbox.length / 2)
})
