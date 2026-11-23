import { describe, expect, it } from 'vitest'

import type { IndexFolder } from '@/api/mail.ts'

import { filterTree, indexTree, matchingSwitchable, switchableUnder, tickState, type IndexNode } from './index-tree.ts'

function folder(path: string, fields: Partial<IndexFolder> = {}): IndexFolder {
  return {
    path,
    delimiter: '.',
    role: null,
    indexed_as: 'folder',
    enabled: false,
    total: 0,
    state: null,
    error: null,
    indexed: null,
    unread: null,
    oldest_indexed: null,
    last_synced_at: null,
    ...fields,
  }
}

function nth(nodes: readonly IndexNode[], index: number): IndexNode {
  const node = nodes[index]
  if (node === undefined) {
    throw new Error(`no node ${String(index)}`)
  }
  return node
}

function paths(nodes: readonly IndexNode[]): unknown[] {
  return nodes.map((node) => (node.children.length === 0 ? node.path : [node.path, paths(node.children)]))
}

describe('indexTree', () => {
  it('nests the folders on their delimiter, roles first, adding the levels the server did not list', () => {
    const tree = indexTree([folder('Work.Done'), folder('Archive'), folder('INBOX', { role: 'inbox', delimiter: null }), folder('Work')])
    expect(paths(tree)).toEqual(['INBOX', 'Archive', ['Work', ['Work.Done']]])
    expect(paths(indexTree([folder('Lists.News')]))).toEqual([['Lists', ['Lists.News']]])
    expect(indexTree([folder('Lists.News')])[0]?.folder).toBeNull()
  })
})

describe('tickState', () => {
  it('is on, off or mixed from the switchable folders at and under a level, null without any', () => {
    const node = nth(indexTree([folder('Work', { enabled: true }), folder('Work.Done'), folder('Work.Live', { indexed_as: 'live' })]), 0)
    expect(tickState(node)).toBe('mixed')
    expect(switchableUnder(node).map((each) => each.path)).toEqual(['Work', 'Work.Done'])
    expect(tickState(nth(node.children, 0))).toBe('off')
    expect(tickState(nth(node.children, 1))).toBeNull()
    expect(tickState(nth(indexTree([folder('A', { enabled: true }), folder('A.B', { enabled: true })]), 0))).toBe('on')
  })
})

describe('filterTree', () => {
  it('keeps the levels matching, in any case, and those above them', () => {
    const tree = indexTree([folder('INBOX'), folder('Work'), folder('Work.Alpha'), folder('Work.Beta')])
    expect(paths(filterTree(tree, 'ALP'))).toEqual([['Work', ['Work.Alpha']]])
    expect(paths(filterTree(tree, '  '))).toEqual(paths(tree))
    expect(filterTree(tree, 'zzz')).toEqual([])
    expect(matchingSwitchable(filterTree(tree, 'alp'), 'alp').map((each) => each.path)).toEqual(['Work.Alpha'])
  })
})
