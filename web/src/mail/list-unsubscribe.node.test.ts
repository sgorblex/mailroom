import { describe, expect, it } from 'vitest'

import type { MessageView } from '@/api/mail.ts'
import { messageItem, messageView, WORK } from '@/test/fake-mail-api.ts'

import { httpsUrl, listName, unsubscribeWay } from './list-unsubscribe.ts'

type MailingList = NonNullable<MessageView['headers']['mailing_list']>

function listed(list: Partial<MailingList>, fields: Partial<MessageView> = {}): MessageView {
  const plain = messageView(messageItem(WORK, 'INBOX', 1))
  return {
    ...plain,
    headers: {
      ...plain.headers,
      mailing_list: {
        id: 'weekly.news.example',
        name: null,
        archive: null,
        post: null,
        help: null,
        unsubscribe_https: null,
        unsubscribe_mailto: null,
        one_click: false,
        unsubscribe_fields: 1,
        unsubscribe_post_fields: 0,
        ...list,
      },
    },
    ...fields,
  }
}

describe('httpsUrl', () => {
  it.each(['https://news.example/u?t=1', 'HTTPS://news.example/'])('takes %s', (value) => {
    expect(httpsUrl(value)?.protocol).toBe('https:')
  })

  it.each([null, 'http://news.example/u', 'javascript:alert(1)', 'mailto:leave@news.example', '//news.example/u', 'not a url'])(
    'refuses %s',
    (value) => {
      expect(httpsUrl(value)).toBeNull()
    },
  )
})

describe('listName', () => {
  it('is the name, else the id', () => {
    expect(listName(listed({ name: 'Weekly news' }).headers.mailing_list ?? ({} as MailingList))).toBe('Weekly news')
    expect(listName(listed({}).headers.mailing_list ?? ({} as MailingList))).toBe('weekly.news.example')
  })
})

describe('unsubscribeWay', () => {
  const https = 'https://news.example/u?t=1'
  const mailto = 'mailto:leave@news.example'

  it('is none without a list or a link', () => {
    expect(unsubscribeWay(messageView(messageItem(WORK, 'INBOX', 1)), true)).toBeNull()
    expect(unsubscribeWay(listed({}), true)).toBeNull()
  })

  it('is one click only when the server said so', () => {
    expect(unsubscribeWay(listed({ unsubscribe_https: https, one_click: true }, { one_click_unsubscribe: true }), true)).toEqual({
      kind: 'oneClick',
      host: 'news.example',
    })
    expect(unsubscribeWay(listed({ unsubscribe_https: https, one_click: true }), true)).toEqual({ kind: 'link', url: https })
  })

  it('prefers the mail to the link, when the account can write mail', () => {
    const both = listed({ unsubscribe_https: https, unsubscribe_mailto: mailto })
    expect(unsubscribeWay(both, true)).toEqual({ kind: 'mail', allowed: true })
    expect(unsubscribeWay(both, false)).toEqual({ kind: 'link', url: https })
    expect(unsubscribeWay(listed({ unsubscribe_mailto: mailto }), false)).toEqual({ kind: 'mail', allowed: false })
  })

  it('never links to anything but https', () => {
    expect(unsubscribeWay(listed({ unsubscribe_https: 'javascript:alert(1)' }, { one_click_unsubscribe: true }), true)).toBeNull()
  })
})
