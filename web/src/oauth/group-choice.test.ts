import { describe, expect, it } from 'vitest'

import type { ToolGroup } from '@/api/oauth.ts'
import { defaultChoice, everyApprovable, toggledChoice } from './group-choice.ts'

function group(name: string, requires: string[] = [], isDefault = true): ToolGroup {
  return { name, description: name, requires, default: isDefault }
}

const SERVED = [
  group('filters_read'),
  group('mail_read'),
  group('mail_organize', ['mail_read']),
  group('mail_send', ['mail_read'], false),
]

describe('tool group choices', () => {
  it('start from the groups offered checked', () => {
    expect(defaultChoice(SERVED)).toEqual(['filters_read', 'mail_read', 'mail_organize'])
  })

  it('keep the served order when a group is checked', () => {
    expect(toggledChoice(SERVED, ['mail_read'], 'filters_read', true)).toEqual([
      'filters_read',
      'mail_read',
    ])
  })

  it('drop the groups that need one unchecked', () => {
    const chosen = ['filters_read', 'mail_read', 'mail_organize', 'mail_send']
    expect(toggledChoice(SERVED, chosen, 'mail_read', false)).toEqual(['filters_read'])
    expect(toggledChoice(SERVED, chosen, 'mail_send', false)).toEqual([
      'filters_read',
      'mail_read',
      'mail_organize',
    ])
  })

  it('approve all leaves out groups whose requirement is not served', () => {
    expect(everyApprovable(SERVED)).toEqual([
      'filters_read',
      'mail_read',
      'mail_organize',
      'mail_send',
    ])
    expect(everyApprovable([group('filters_read'), group('mail_send', ['mail_read'])])).toEqual([
      'filters_read',
    ])
  })
})
