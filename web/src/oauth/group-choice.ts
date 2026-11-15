import type { TFunction } from 'i18next'

import type { ToolGroup } from '@/api/oauth.ts'

/** The groups offered checked. */
export function defaultChoice(groups: readonly ToolGroup[]): string[] {
  return groups.filter((group) => group.default).map((group) => group.name)
}

/**
 * `chosen` with `name` checked or not. Unchecking a group unchecks those that require it, which
 * cannot be approved without it.
 */
export function toggledChoice(
  groups: readonly ToolGroup[],
  chosen: readonly string[],
  name: string,
  checked: boolean,
): string[] {
  if (checked) {
    return groups.map((group) => group.name).filter((each) => each === name || chosen.includes(each))
  }
  const dropped = new Set([name])
  for (const group of groups) {
    if (group.requires.some((required) => dropped.has(required))) {
      dropped.add(group.name)
    }
  }
  return chosen.filter((each) => !dropped.has(each))
}

/** A group's name in the user's language; its own name when there is no translation. */
export function groupLabel(t: TFunction, name: string): string {
  return t(`toolGroups.${name}.label`, { defaultValue: name })
}

/** Every served group whose requirements are served too: what Approve all asks for. */
export function everyApprovable(served: readonly ToolGroup[]): string[] {
  let names = served.map((group) => group.name)
  for (;;) {
    const kept = served
      .filter((group) => names.includes(group.name))
      .filter((group) => group.requires.every((required) => names.includes(required)))
      .map((group) => group.name)
    if (kept.length === names.length) {
      return kept
    }
    names = kept
  }
}
