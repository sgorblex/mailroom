import { useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { ToolGroup } from '@/api/oauth.ts'
import { groupLabel, toggledChoice } from '@/oauth/group-choice.ts'

/**
 * The tool groups an app may use, one checkbox each, labelled and described in the user's
 * language. A group is disabled until the groups it requires are checked.
 */
export function GroupChoices({
  groups,
  chosen,
  onChange,
  disabled = false,
}: {
  groups: readonly ToolGroup[]
  chosen: readonly string[]
  onChange: (chosen: string[]) => void
  disabled?: boolean
}): ReactElement {
  const { t } = useTranslation()
  const id = useId()
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="mb-1 text-sm font-medium">{t('toolGroups.legend')}</legend>
      {groups.map((group) => {
        const missing = group.requires.filter((required) => !chosen.includes(required))
        const labelledBy = `${id}-${group.name}-label`
        const describedBy = `${id}-${group.name}-description`
        return (
          <label key={group.name} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={chosen.includes(group.name)}
              disabled={missing.length > 0}
              aria-labelledby={labelledBy}
              aria-describedby={describedBy}
              onChange={(event) => {
                onChange(toggledChoice(groups, chosen, group.name, event.target.checked))
              }}
            />
            <span className="flex flex-col">
              <span id={labelledBy}>{groupLabel(t, group.name)}</span>
              <span id={describedBy} className="text-xs text-muted-foreground">
                {t(`toolGroups.${group.name}.description`, { defaultValue: group.description })}
                {missing.length > 0 &&
                  ` ${t('toolGroups.needs', {
                    groups: missing.map((name) => groupLabel(t, name)).join(', '),
                  })}`}
              </span>
            </span>
          </label>
        )
      })}
    </fieldset>
  )
}
