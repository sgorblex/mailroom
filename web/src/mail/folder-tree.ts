import { FOLDER_ROLES, type MailFolder } from '@/api/mail.ts'

import { isSameFolder } from './triage-context.ts'

/** A level of an account's folder tree: a listed folder, or one the server did not list (`folder: null`). */
export interface FolderNode {
  /** The path as the server names it; an unlisted level's is its segments joined by the delimiter. */
  path: string
  /** The last segment of the path. */
  name: string
  folder: MailFolder | null
  children: FolderNode[]
}

/** A node as the tree shows it, in order, with its level (1 at the top) and parent's path. */
export interface VisibleNode {
  node: FolderNode
  level: number
  parent: string | null
}

/** The segments of a path, split on the account's delimiter (none: the whole path). */
export function pathSegments(path: string, delimiter: string | null): string[] {
  return delimiter === null || delimiter === '' ? [path] : path.split(delimiter)
}

/** The paths of the levels above `path`, outermost first. */
export function ancestorPaths(path: string, delimiter: string | null): string[] {
  const segments = pathSegments(path, delimiter)
  return segments.slice(1).map((_, index) => segments.slice(0, index + 1).join(delimiter ?? ''))
}

const UNRANKED = FOLDER_ROLES.length

function rank(node: FolderNode): number {
  const role = node.folder?.role ?? null
  return role === null ? UNRANKED : FOLDER_ROLES.indexOf(role)
}

function compare(a: FolderNode, b: FolderNode): number {
  return rank(a) - rank(b) || a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
}

/**
 * The account's folders as a tree: each path split on its delimiter, a level the server did not
 * list added as a node without a folder. On every level the folders with a role come first, in
 * `FOLDER_ROLES` order, then the others by name.
 */
export function folderTree(folders: readonly MailFolder[]): FolderNode[] {
  const roots: FolderNode[] = []
  const nodes = new Map<string, FolderNode>()
  for (const folder of folders) {
    const segments = pathSegments(folder.path, folder.delimiter)
    let siblings = roots
    segments.forEach((name, index) => {
      const last = index === segments.length - 1
      const path = last ? folder.path : segments.slice(0, index + 1).join(folder.delimiter ?? '')
      let node = nodes.get(path)
      if (node === undefined) {
        node = { path, name, folder: null, children: [] }
        nodes.set(path, node)
        siblings.push(node)
      }
      if (last) {
        node.folder = folder
      }
      siblings = node.children
    })
  }
  const sorted = (level: FolderNode[]): FolderNode[] =>
    level.sort(compare).map((node) => ({ ...node, children: sorted(node.children) }))
  return sorted(roots)
}

/** Folders that show mail kept elsewhere too (Gmail's All Mail and Starred): their unread counts it again. */
const VIRTUAL_ROLES: ReadonlySet<string> = new Set(['all', 'flagged'])

function ownUnread(node: FolderNode): number {
  return node.folder === null || VIRTUAL_ROLES.has(node.folder.role ?? '') ? 0 : node.folder.unread
}

/** The unread messages of `node` and every folder under it, each counted once. */
export function subtreeUnread(node: FolderNode): number {
  return node.children.reduce((sum, child) => sum + subtreeUnread(child), ownUnread(node))
}

/** Roles whose folders a folder's subfolders leave out, unless it has the same role. */
const APART_ROLES: ReadonlySet<string> = new Set(['sent', 'drafts', 'all', 'archive', 'junk', 'trash'])

/**
 * The subfolders a list of `node` with its subfolders reads besides itself, as mailroom-core's
 * `with_subfolders` picks them: selectable, at any depth, none with a role kept apart (Sent,
 * Drafts, All Mail, Archive, Junk, Trash) or under one, unless `node` has that role.
 */
export function listedSubfolders(node: FolderNode): FolderNode[] {
  const role = node.folder?.role ?? null
  const walk = (level: readonly FolderNode[]): FolderNode[] =>
    level.flatMap((child) => {
      const apart = child.folder?.role != null && APART_ROLES.has(child.folder.role) && child.folder.role !== role
      return apart ? [] : [...(child.folder?.selectable === true ? [child] : []), ...walk(child.children)]
    })
  return walk(node.children)
}

/** Of a folder listed with its subfolders, the folders at most, itself included: mailroom-core's `MAX_LISTED_FOLDERS`. */
export const MAX_LISTED_FOLDERS = 50

/** A folder that can be listed with subfolders of its own, within the server's cap. */
export function hasListedSubfolders(node: FolderNode): boolean {
  const count = node.folder?.selectable === true ? listedSubfolders(node).length : 0
  return count > 0 && count + 1 <= MAX_LISTED_FOLDERS
}

/** The node of the folder at `path`, if `roots` has it. */
export function findNode(roots: readonly FolderNode[], path: string): FolderNode | undefined {
  for (const node of roots) {
    if (node.folder !== null && isSameFolder(node.folder.path, path)) {
      return node
    }
    const found = findNode(node.children, path)
    if (found !== undefined) {
      return found
    }
  }
  return undefined
}

/** The unread messages of `node` with its subfolders, as that list reads them, each counted once. */
export function listedUnread(node: FolderNode): number {
  return listedSubfolders(node).reduce((sum, child) => sum + ownUnread(child), ownUnread(node))
}

/** The nodes shown when the levels in `expanded` are open, top to bottom. */
export function visibleNodes(roots: readonly FolderNode[], expanded: ReadonlySet<string>): VisibleNode[] {
  const shown: VisibleNode[] = []
  const walk = (level: readonly FolderNode[], depth: number, parent: string | null): void => {
    for (const node of level) {
      shown.push({ node, level: depth, parent })
      if (node.children.length > 0 && expanded.has(node.path)) {
        walk(node.children, depth + 1, node.path)
      }
    }
  }
  walk(roots, 1, null)
  return shown
}

/** The account's hierarchy delimiter: the first a folder gives (INBOX may give none). */
export function accountDelimiter(folders: readonly MailFolder[]): string | null {
  return folders.find((folder) => folder.delimiter !== null)?.delimiter ?? null
}

/** The levels open at first: those above each folder with a role. */
export function initiallyExpanded(folders: readonly MailFolder[]): string[] {
  const open = new Set<string>()
  for (const folder of folders) {
    if (folder.role !== null) {
      for (const path of ancestorPaths(folder.path, folder.delimiter)) {
        open.add(path)
      }
    }
  }
  return [...open]
}

/** Why an action is not offered on a folder; `null` where it is. */
export type FolderRefusal = 'protected' | 'holdsRole' | 'hasSubfolders' | null

/** A folder with a role, or the inbox, by name in any case. */
function isProtected(folder: MailFolder): boolean {
  return folder.role !== null || folder.path.toUpperCase() === 'INBOX'
}

function holdsProtected(node: FolderNode): boolean {
  return node.children.some((child) => (child.folder !== null && isProtected(child.folder)) || holdsProtected(child))
}

/**
 * What the server would refuse, said before asking it: INBOX and the folders with a role are
 * never renamed or deleted, nor a folder holding one renamed, nor a folder with subfolders deleted.
 */
export function folderRefusals(node: FolderNode & { folder: MailFolder }): { rename: FolderRefusal; delete: FolderRefusal } {
  if (isProtected(node.folder)) {
    return { rename: 'protected', delete: 'protected' }
  }
  return {
    rename: holdsProtected(node) ? 'holdsRole' : null,
    delete: node.children.length > 0 ? 'hasSubfolders' : null,
  }
}

export const MAX_FOLDER_NAME = 255

export type NameProblem = 'empty' | 'tooLong' | 'characters' | 'spaces' | 'inbox'

// eslint-disable-next-line no-control-regex -- control characters are what it finds
const CONTROL = /[\u0000-\u001f\u007f]/

/** What mailroom-core would refuse in a folder's name (`docs/core.md`, *Folders*); `null` when nothing. */
export function nameProblem(name: string, delimiter: string | null, parent: string | null): NameProblem | null {
  if (name === '') {
    return 'empty'
  }
  if (name.length > MAX_FOLDER_NAME) {
    return 'tooLong'
  }
  if (CONTROL.test(name) || name.includes('*') || name.includes('%') || (delimiter !== null && delimiter !== '' && name.includes(delimiter))) {
    return 'characters'
  }
  if (name.trim() !== name) {
    return 'spaces'
  }
  if (parent === null && name.toUpperCase() === 'INBOX') {
    return 'inbox'
  }
  return null
}

/** The path of `name` under `parent` (`null`: the top level). */
export function childPath(parent: string | null, name: string, delimiter: string | null): string {
  return parent === null ? name : `${parent}${delimiter ?? ''}${name}`
}

/** `path` once `from` is renamed to `to`: itself or a folder under it moves along; any other stays. */
export function renamedPath(path: string, from: string, to: string, delimiter: string | null): string {
  return isWithin(path, from, delimiter) ? to + path.slice(from.length) : path
}

/** `path` and every folder under it. */
export function isWithin(path: string, folder: string, delimiter: string | null): boolean {
  return path === folder || (delimiter !== null && delimiter !== '' && path.startsWith(folder + delimiter))
}
