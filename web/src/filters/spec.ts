/**
 * The editor's form of an imap-thingy filter spec, and the way back.
 *
 * A spec is `{"criterion": …, "action": …}`. A criterion is a leaf `{"type", "args"}` or a group:
 * `{"and": […]}`, `{"or": […]}`, `{"not": …}`. An action is a leaf or `{"seq": […]}`, which the
 * editor keeps as a flat list of leaves. Every node carries a `key` for React; specs never do.
 */

import { initialArg, type LeafDef, type Vocabulary } from './vocabulary.ts'

export type Args = Record<string, unknown>

export interface Leaf {
  key: string
  type: string
  args: Args
}

export type CriterionNode =
  | Leaf & { kind: 'leaf' }
  | { key: string; kind: 'and' | 'or'; children: CriterionNode[] }
  | { key: string; kind: 'not'; child: CriterionNode }

export interface SpecModel {
  criterion: CriterionNode
  actions: Leaf[]
}

let lastKey = 0
export function newKey(): string {
  lastKey += 1
  return `node-${String(lastKey)}`
}

/** Why a spec is not one: the editor words each in the reader's language. */
export type SpecShapeProblem = 'notSpec' | 'notObject' | 'noType' | 'argsNotObject' | 'emptyList'

const SHAPE_MESSAGES: Record<SpecShapeProblem, string> = {
  notSpec: 'The spec must be an object with "criterion" and "action".',
  notObject: 'expected an object.',
  noType: 'expected "type", "and", "or" or "not".',
  argsNotObject: '"args" must be an object.',
  emptyList: 'needs a non-empty list.',
}

export class SpecShapeError extends Error {
  /** Where in the spec, as a path (`criterion.and[0]`); `''` for the spec itself. */
  readonly where: string
  readonly problem: SpecShapeProblem
  /** The group whose list is empty (`and`, `or`, `seq`), for `emptyList`. */
  readonly group: string | undefined

  constructor(where: string, problem: SpecShapeProblem, group?: string) {
    const detail = group === undefined ? SHAPE_MESSAGES[problem] : `"${group}" ${SHAPE_MESSAGES[problem]}`
    super(where === '' ? detail : `${where}: ${detail}`)
    this.name = 'SpecShapeError'
    this.where = where
    this.problem = problem
    this.group = group
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readLeaf(value: Record<string, unknown>, where: string): Leaf {
  if (typeof value['type'] !== 'string') {
    throw new SpecShapeError(where, 'noType')
  }
  const args = value['args'] ?? {}
  if (!isObject(args)) {
    throw new SpecShapeError(where, 'argsNotObject')
  }
  return { key: newKey(), type: value['type'], args: { ...args } }
}

function readCriterion(value: unknown, where: string): CriterionNode {
  if (!isObject(value)) {
    throw new SpecShapeError(where, 'notObject')
  }
  for (const kind of ['and', 'or'] as const) {
    if (kind in value) {
      const children = value[kind]
      if (!Array.isArray(children) || children.length === 0) {
        throw new SpecShapeError(where, 'emptyList', kind)
      }
      return {
        key: newKey(),
        kind,
        children: children.map((child, index) => readCriterion(child, `${where}.${kind}[${String(index)}]`)),
      }
    }
  }
  if ('not' in value) {
    return { key: newKey(), kind: 'not', child: readCriterion(value['not'], `${where}.not`) }
  }
  return { ...readLeaf(value, where), kind: 'leaf' }
}

function readActions(value: unknown, where: string): Leaf[] {
  if (!isObject(value)) {
    throw new SpecShapeError(where, 'notObject')
  }
  if ('seq' in value) {
    const steps = value['seq']
    if (!Array.isArray(steps) || steps.length === 0) {
      throw new SpecShapeError(where, 'emptyList', 'seq')
    }
    return steps.flatMap((step, index) => readActions(step, `${where}.seq[${String(index)}]`))
  }
  return [readLeaf(value, where)]
}

/** Reads a spec into the editor's form; throws `SpecShapeError` saying where and what went wrong. */
export function readSpec(spec: unknown): SpecModel {
  if (!isObject(spec)) {
    throw new SpecShapeError('', 'notSpec')
  }
  return {
    criterion: readCriterion(spec['criterion'], 'criterion'),
    actions: readActions(spec['action'], 'action'),
  }
}

function writeLeaf(leaf: Leaf): Record<string, unknown> {
  const args = Object.fromEntries(Object.entries(leaf.args).filter(([, value]) => value !== undefined))
  return Object.keys(args).length === 0 ? { type: leaf.type } : { type: leaf.type, args }
}

function writeCriterion(node: CriterionNode): Record<string, unknown> {
  switch (node.kind) {
    case 'and':
    case 'or':
      return { [node.kind]: node.children.map(writeCriterion) }
    case 'not':
      return { not: writeCriterion(node.child) }
    case 'leaf':
      return writeLeaf(node)
  }
}

/** The spec the API stores; one action is a leaf, several a `seq`. */
export function writeSpec(model: SpecModel): Record<string, unknown> {
  const [only, ...rest] = model.actions
  const action = only !== undefined && rest.length === 0 ? writeLeaf(only) : { seq: model.actions.map(writeLeaf) }
  return { criterion: writeCriterion(model.criterion), action }
}

/** Today's date as `YYYY-MM-DD`, local time: what a fresh date argument starts at. */
export function todayIso(): string {
  const now = new Date()
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${String(now.getFullYear())}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * A leaf of `type`, its arguments at their starting values; arguments `previous` also has (same
 * name, as from "from contains" to "from is") keep their value.
 */
export function freshLeaf(defs: LeafDef[], type: string, previous: Args = {}): Leaf {
  const def = defs.find((candidate) => candidate.type === type)
  const args: Args = {}
  for (const arg of def?.args ?? []) {
    args[arg.name] = arg.name in previous ? previous[arg.name] : initialArg(arg, todayIso())
  }
  return { key: newKey(), type, args }
}

function preferred(defs: LeafDef[], type: string): string {
  return defs.some((def) => def.type === type) ? type : (defs[0]?.type ?? type)
}

/** What a new filter starts as: mail from someone, moved to a folder still to be chosen. */
export function newSpecModel(vocabulary: Vocabulary): SpecModel {
  return {
    criterion: { ...freshLeaf(vocabulary.criteria, preferred(vocabulary.criteria, 'from_contains')), kind: 'leaf' },
    actions: [freshLeaf(vocabulary.actions, preferred(vocabulary.actions, 'move_to'))],
  }
}

export function newCriterionLeaf(vocabulary: Vocabulary): CriterionNode {
  return { ...freshLeaf(vocabulary.criteria, preferred(vocabulary.criteria, 'from_contains')), kind: 'leaf' }
}

export function newActionLeaf(vocabulary: Vocabulary): Leaf {
  return freshLeaf(vocabulary.actions, preferred(vocabulary.actions, 'mark_as_read'))
}

export type CriterionKind = 'and' | 'or' | 'not' | `leaf:${string}`

export function kindOf(node: CriterionNode): CriterionKind {
  return node.kind === 'leaf' ? `leaf:${node.type}` : node.kind
}

/**
 * `node` turned into `kind`: a leaf type keeps the arguments the two share; a group keeps what
 * it holds (`and` ↔ `or`), or takes the node in (leaf → group, anything → `not`).
 */
export function changeKind(vocabulary: Vocabulary, node: CriterionNode, kind: CriterionKind): CriterionNode {
  // The node keeps its key, so its editor (and the focused type picker) stays where it is; what
  // it takes in gets a key of its own.
  if (kind.startsWith('leaf:')) {
    const type = kind.slice('leaf:'.length)
    const previous = node.kind === 'leaf' ? node.args : {}
    return { ...freshLeaf(vocabulary.criteria, type, previous), key: node.key, kind: 'leaf' }
  }
  const inner: CriterionNode = { ...node, key: newKey() }
  if (kind === 'not') {
    return { key: node.key, kind: 'not', child: inner }
  }
  const group = kind as 'and' | 'or'
  switch (node.kind) {
    case 'and':
    case 'or':
      return { ...node, kind: group }
    case 'not':
      return { key: node.key, kind: group, children: [node.child] }
    case 'leaf':
      return { key: node.key, kind: group, children: [inner] }
  }
}

/** Whether a required argument is still empty (a folder not chosen, a text not typed). */
export function missingArgs(model: SpecModel, vocabulary: Vocabulary): boolean {
  const leafMissing = (defs: LeafDef[], leaf: Leaf): boolean => {
    const def = defs.find((candidate) => candidate.type === leaf.type)
    return (def?.args ?? []).some((arg) => {
      const value = leaf.args[arg.name]
      if (arg.field.kind === 'folder' && !arg.field.optional) {
        return !Array.isArray(value) || value.length === 0
      }
      if (!arg.required) {
        return false
      }
      switch (arg.field.kind) {
        case 'text':
        case 'date':
          return typeof value !== 'string' || value === ''
        case 'integer':
          return typeof value !== 'number' || !Number.isInteger(value)
        default:
          return value === undefined
      }
    })
  }
  const criterionMissing = (node: CriterionNode): boolean => {
    switch (node.kind) {
      case 'and':
      case 'or':
        return node.children.some(criterionMissing)
      case 'not':
        return criterionMissing(node.child)
      case 'leaf':
        return leafMissing(vocabulary.criteria, node)
    }
  }
  return criterionMissing(model.criterion) || model.actions.some((action) => leafMissing(vocabulary.actions, action))
}
