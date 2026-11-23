import type { IndexFolder, MailFolder } from '@/api/mail.ts'
import { folderTree, type FolderNode } from '@/mail/folder-tree.ts'

/** A level of an account's folders as the index settings show them; `folder: null` for a level the server did not list. */
export interface IndexNode {
  path: string
  name: string
  folder: IndexFolder | null
  children: IndexNode[]
}

/** What a level's checkbox shows: every switchable folder at or under it on, none, some; `null` when none can be switched. */
export type TickState = 'on' | 'off' | 'mixed' | null

/** The folder as the mail tree takes it: only its path, delimiter and role order it. */
function asMailFolder(folder: IndexFolder): MailFolder {
  return {
    path: folder.path,
    role: folder.role,
    delimiter: folder.delimiter,
    unread: 0,
    total: 0,
    selectable: true,
    uid_validity: null,
    uid_next: null,
  }
}

/**
 * The account's folders as a tree, as the mail screen's folder pane builds it (`folderTree`): each
 * path split on its delimiter, the levels the server did not list added without a folder, and on
 * every level the folders with a role first.
 */
export function indexTree(folders: readonly IndexFolder[]): IndexNode[] {
  const byPath = new Map(folders.map((folder) => [folder.path, folder]))
  const convert = (node: FolderNode): IndexNode => ({
    path: node.path,
    name: node.name,
    folder: node.folder === null ? null : (byPath.get(node.folder.path) ?? null),
    children: node.children.map(convert),
  })
  return folderTree(folders.map(asMailFolder)).map(convert)
}

/** A folder the user can switch on or off the index (not a Gmail label, not a live folder). */
export function isSwitchable(folder: IndexFolder | null): folder is IndexFolder {
  return folder?.indexed_as === 'folder'
}

/** The switchable folders at and under `node`, top to bottom. */
export function switchableUnder(node: IndexNode): IndexFolder[] {
  return [...(isSwitchable(node.folder) ? [node.folder] : []), ...node.children.flatMap(switchableUnder)]
}

/** What `node`'s checkbox shows, from the switchable folders at and under it. */
export function tickState(node: IndexNode): TickState {
  const folders = switchableUnder(node)
  if (folders.length === 0) {
    return null
  }
  const on = folders.filter((folder) => folder.enabled).length
  return on === folders.length ? 'on' : on === 0 ? 'off' : 'mixed'
}

/** Whether `path` matches the filter `query`: its text anywhere in the path, in any case; an empty query matches all. */
export function matchesFilter(path: string, query: string): boolean {
  const wanted = query.trim().toLocaleLowerCase()
  return wanted === '' || path.toLocaleLowerCase().includes(wanted)
}

/** The tree with only the levels matching `query` and the levels above them. */
export function filterTree(roots: readonly IndexNode[], query: string): IndexNode[] {
  return roots.flatMap((node) => {
    const children = filterTree(node.children, query)
    return matchesFilter(node.path, query) || children.length > 0 ? [{ ...node, children }] : []
  })
}

/** The switchable folders of `roots` matching `query`, those All and None act on. */
export function matchingSwitchable(roots: readonly IndexNode[], query: string): IndexFolder[] {
  return roots.flatMap(switchableUnder).filter((folder) => matchesFilter(folder.path, query))
}
