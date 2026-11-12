import { describe, expect, it } from 'vitest'

import type { MailFolder } from '@/api/mail.ts'

import { accountDelimiter, ancestorPaths, folderTree, initiallyExpanded, subtreeUnread, visibleNodes, type FolderNode } from './folder-tree.ts'

function folder(path: string, changes: Partial<MailFolder> = {}): MailFolder {
  return { path, role: null, unread: 0, total: 0, delimiter: '/', selectable: true, ...changes }
}

/** Each node as `path` (`*` when the server did not list it), its children indented. */
function outline(nodes: FolderNode[], depth = 0): string[] {
  return nodes.flatMap((node) => [
    `${'  '.repeat(depth)}${node.path}${node.folder === null ? '*' : ''}`,
    ...outline(node.children, depth + 1),
  ])
}

describe('folderTree', () => {
  it('nests on the delimiter, roles first in their order, then by name', () => {
    const tree = folderTree([
      folder('Work/2026'),
      folder('Trash', { role: 'trash' }),
      folder('archive'),
      folder('Work'),
      folder('Sent', { role: 'sent' }),
      folder('INBOX', { role: 'inbox' }),
      folder('Work/2025'),
      folder('Work/10'),
      folder('Work/9'),
    ])
    expect(outline(tree)).toEqual([
      'INBOX',
      'Sent',
      'Trash',
      'archive',
      'Work',
      '  Work/9',
      '  Work/10',
      '  Work/2025',
      '  Work/2026',
    ])
    expect(tree[4]?.children.map((node) => node.name)).toEqual(['9', '10', '2025', '2026'])
  })

  it('adds the levels the server did not list, and keeps the \\Noselect ones it did', () => {
    const tree = folderTree([
      folder('[Gmail]', { selectable: false }),
      folder('[Gmail]/Sent Mail', { role: 'sent' }),
      folder('Lists/Nerds/Rust'),
    ])
    expect(outline(tree)).toEqual(['[Gmail]', '  [Gmail]/Sent Mail', 'Lists*', '  Lists/Nerds*', '    Lists/Nerds/Rust'])
    expect(tree[0]?.folder?.selectable).toBe(false)
  })

  it('keeps each path whole on a server without a delimiter, or with another one', () => {
    expect(outline(folderTree([folder('a/b', { delimiter: null }), folder('a.b', { delimiter: null })]))).toEqual([
      'a.b',
      'a/b',
    ])
    expect(outline(folderTree([folder('INBOX.Sub', { delimiter: '.' }), folder('INBOX', { delimiter: '.', role: 'inbox' })]))).toEqual(
      ['INBOX', '  INBOX.Sub'],
    )
  })
})

describe('the tree helpers', () => {
  const tree = folderTree([
    folder('INBOX', { role: 'inbox', unread: 2 }),
    folder('Lists', { unread: 1 }),
    folder('Lists/Rust', { unread: 4 }),
    folder('Lists/Rust/Async', { unread: 5 }),
  ])

  it('sums the unread of a subtree, once: not again for All Mail or Starred', () => {
    expect(tree.map(subtreeUnread)).toEqual([2, 10])
    const gmail = folderTree([
      folder('[Gmail]', { selectable: false }),
      folder('[Gmail]/All Mail', { role: 'all', unread: 9 }),
      folder('[Gmail]/Starred', { role: 'flagged', unread: 2 }),
      folder('[Gmail]/Spam', { role: 'junk', unread: 3 }),
    ])
    expect(gmail.map(subtreeUnread)).toEqual([3])
  })

  it('shows only what the open levels hold, with levels and parents', () => {
    expect(visibleNodes(tree, new Set()).map(({ node }) => node.path)).toEqual(['INBOX', 'Lists'])
    expect(visibleNodes(tree, new Set(['Lists/Rust']))).toHaveLength(2)
    expect(
      visibleNodes(tree, new Set(['Lists', 'Lists/Rust'])).map(({ node, level, parent }) => [node.path, level, parent]),
    ).toEqual([
      ['INBOX', 1, null],
      ['Lists', 1, null],
      ['Lists/Rust', 2, 'Lists'],
      ['Lists/Rust/Async', 3, 'Lists/Rust'],
    ])
  })

  it('opens at first the levels above each role folder', () => {
    expect(ancestorPaths('a/b/c', '/')).toEqual(['a', 'a/b'])
    expect(ancestorPaths('a/b', null)).toEqual([])
    const folders = [
      folder('[Gmail]/Sent Mail', { role: 'sent' }),
      folder('Lists/Rust/Async'),
      folder('Other/Deep'),
    ]
    expect(initiallyExpanded(folders)).toEqual(['[Gmail]'])
    expect(accountDelimiter([folder('INBOX', { delimiter: null }), folder('A.B', { delimiter: '.' })])).toBe('.')
  })
})
