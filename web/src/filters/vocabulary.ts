/**
 * The criteria and actions a filter can use, read from imap-thingy's JSON Schema of a filter spec
 * (`GET /filters/schema`), so a new imap-thingy leaf shows up in the editor without a web change.
 */

/**
 * How an argument is edited. A folder is a path of segments; `optional` allows none (`null`). An
 * argument of a shape the editor does not know is `unknown`: only the JSON editor sets it.
 */
export type ArgField =
  | { kind: 'text' }
  | { kind: 'date' }
  | { kind: 'boolean' }
  | { kind: 'integer'; minimum: number | undefined; maximum: number | undefined }
  | { kind: 'choice'; options: string[] }
  | { kind: 'folder'; optional: boolean }
  | { kind: 'unknown' }

/** imap-thingy's folder path, by the name its schema defines it under. */
const FOLDER_REF = '#/$defs/path'

/** The strings an `enum` or a `const` allows; `undefined` when it has neither. */
function allowedStrings(node: JsonObject): string[] | undefined {
  if (Array.isArray(node['enum'])) {
    return node['enum'].filter((value) => typeof value === 'string')
  }
  return typeof node['const'] === 'string' ? [node['const']] : undefined
}

export interface ArgDef {
  name: string
  required: boolean
  field: ArgField
}

/** A leaf criterion or action: its `type` and the arguments it takes. */
export interface LeafDef {
  type: string
  args: ArgDef[]
}

export interface Vocabulary {
  criteria: LeafDef[]
  actions: LeafDef[]
}

type JsonObject = Record<string, unknown>

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export class VocabularyError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VocabularyError'
  }
}

/** Reads the leaves of the schema's `criterion` and `action` definitions. */
export function readVocabulary(schema: unknown): Vocabulary {
  if (!isObject(schema) || !isObject(schema['$defs'])) {
    throw new VocabularyError('The filter schema has no definitions.')
  }
  const defs = schema['$defs']
  const resolve = (node: unknown): unknown => {
    let current = node
    // A chain of references ends: imap-thingy's schema has no reference cycle without an object.
    for (let depth = 0; depth < 16 && isObject(current) && typeof current['$ref'] === 'string'; depth++) {
      const name = /^#\/\$defs\/(.+)$/.exec(current['$ref'])?.[1]
      current = name === undefined ? undefined : defs[name]
    }
    return current
  }

  function leaves(node: unknown, seen: Set<unknown>): LeafDef[] {
    const resolved = resolve(node)
    if (!isObject(resolved) || seen.has(resolved)) {
      return []
    }
    seen.add(resolved)
    const variants = resolved['oneOf'] ?? resolved['anyOf']
    if (Array.isArray(variants)) {
      return variants.flatMap((variant) => leaves(variant, seen))
    }
    const properties = resolved['properties']
    if (!isObject(properties) || !isObject(properties['type'])) {
      return []
    }
    const types = allowedStrings(properties['type'])
    if (types === undefined) {
      return []
    }
    const args = readArgs(properties['args'])
    return types.map((type) => ({ type, args }))
  }

  function readArgs(node: unknown): ArgDef[] {
    const resolved = resolve(node)
    if (!isObject(resolved) || !isObject(resolved['properties'])) {
      return []
    }
    const required = Array.isArray(resolved['required']) ? resolved['required'] : []
    return Object.entries(resolved['properties']).map(([name, property]) => ({
      name,
      required: required.includes(name),
      field: readField(property),
    }))
  }

  function readField(node: unknown): ArgField {
    if (isObject(node) && node['$ref'] === FOLDER_REF) {
      return { kind: 'folder', optional: false }
    }
    const resolved = resolve(node)
    if (!isObject(resolved)) {
      return { kind: 'unknown' }
    }
    const alternatives = resolved['anyOf'] ?? resolved['oneOf']
    if (Array.isArray(alternatives)) {
      const present = alternatives.filter((option) => !(isObject(option) && option['type'] === 'null'))
      const field = present.length === 1 ? readField(present[0]) : { kind: 'unknown' as const }
      return field.kind === 'folder' && present.length < alternatives.length
        ? { kind: 'folder', optional: true }
        : field
    }
    const options = allowedStrings(resolved)
    if (options !== undefined) {
      return { kind: 'choice', options }
    }
    const number = (name: string): number | undefined =>
      typeof resolved[name] === 'number' ? resolved[name] : undefined
    switch (resolved['type']) {
      case 'boolean':
        return { kind: 'boolean' }
      case 'integer':
        return { kind: 'integer', minimum: number('minimum'), maximum: number('maximum') }
      case 'string':
        return resolved['format'] === 'date' ? { kind: 'date' } : { kind: 'text' }
      default:
        return { kind: 'unknown' }
    }
  }

  const criteria = leaves(defs['criterion'], new Set())
  const actions = leaves(defs['action'], new Set())
  if (criteria.length === 0 || actions.length === 0) {
    throw new VocabularyError('The filter schema lists no criteria or no actions.')
  }
  return { criteria, actions }
}

/** The value a fresh argument starts with; `undefined` leaves an optional one out. */
export function initialArg(arg: ArgDef, today: string): unknown {
  switch (arg.field.kind) {
    case 'text':
      return arg.required ? '' : undefined
    case 'date':
      return today
    case 'boolean':
      return arg.required ? false : undefined
    case 'integer':
      return arg.field.minimum ?? 0
    case 'choice':
      return arg.field.options[0]
    case 'folder':
      // An empty path is "not chosen yet": the folder picker asks for one.
      return arg.field.optional ? undefined : []
    case 'unknown':
      return undefined
  }
}
