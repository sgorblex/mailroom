import { FOLDER_ROLES, type MailFolder } from '@/api/mail.ts'

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

/** The unread messages of `node` and every folder under it, each counted once. */
export function subtreeUnread(node: FolderNode): number {
  const own = node.folder === null || VIRTUAL_ROLES.has(node.folder.role ?? '') ? 0 : node.folder.unread
  return node.children.reduce((sum, child) => sum + subtreeUnread(child), own)
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
