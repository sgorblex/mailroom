import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/components/ui/input.tsx'

import { folderLabel } from './describe.ts'
import type { FoldersState } from './folders.ts'
import { FILTERS_NS } from './i18n.ts'

export const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm aria-invalid:border-destructive'

/**
 * Picks one of the account's folders. `null` is no folder: "choose one" unless `noneLabel` names
 * what none means (the trash action's default folder). When the folders cannot be listed, the
 * path is typed instead, its segments separated by `/`.
 */
export function FolderPicker({
  id,
  folders,
  value,
  onChange,
  noneLabel,
  describedBy,
  invalid = false,
}: {
  id: string
  folders: FoldersState
  value: readonly string[] | null
  onChange: (path: string[] | null) => void
  noneLabel?: string
  describedBy?: string
  invalid?: boolean
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const chosen = value === null || value.length === 0 ? null : value

  if (folders.kind === 'failed') {
    return (
      <TypedFolder
        id={id}
        value={chosen}
        onChange={onChange}
        required={noneLabel === undefined}
        describedBy={describedBy}
        invalid={invalid}
      />
    )
  }

  const paths = folders.kind === 'loaded' ? folders.paths : []
  const key = (path: readonly string[]): string => JSON.stringify(path)
  // A folder gone from the server still shows, so the stored choice is not silently changed.
  const known = chosen === null || paths.some((path) => key(path) === key(chosen))
  return (
    <select
      id={id}
      aria-describedby={describedBy}
      aria-invalid={invalid}
      required={noneLabel === undefined}
      disabled={folders.kind === 'loading'}
      className={selectClass}
      value={chosen === null ? '' : key(chosen)}
      onChange={(event) => {
        onChange(event.target.value === '' ? null : (JSON.parse(event.target.value) as string[]))
      }}
    >
      <option value="">
        {folders.kind === 'loading' ? t('filters.foldersLoading') : (noneLabel ?? t('filters.chooseFolder'))}
      </option>
      {!known && <option value={key(chosen)}>{folderLabel(chosen)}</option>}
      {paths.map((path) => (
        <option key={key(path)} value={key(path)}>
          {folderLabel(path)}
        </option>
      ))}
    </select>
  )
}

function segmentsOf(text: string): string[] | null {
  const segments = text.split('/').filter((segment) => segment !== '')
  return segments.length === 0 ? null : segments
}

/**
 * A folder path typed as text, its segments separated by `/`. The text is kept as typed, so a
 * trailing `/` stays while the next segment is on its way.
 */
function TypedFolder({
  id,
  value,
  onChange,
  required,
  describedBy,
  invalid,
}: {
  id: string
  value: readonly string[] | null
  onChange: (path: string[] | null) => void
  required: boolean
  describedBy: string | undefined
  invalid: boolean
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const joined = value === null ? '' : value.join('/')
  const [text, setText] = useState(joined)
  // A value changed from outside (another type of action, say) shows as it is.
  const shown = (segmentsOf(text)?.join('/') ?? '') === joined ? text : joined
  return (
    <Input
      id={id}
      aria-describedby={describedBy}
      aria-invalid={invalid}
      required={required}
      placeholder={t('filters.folderTyped')}
      autoCapitalize="none"
      spellCheck={false}
      value={shown}
      onChange={(event) => {
        setText(event.target.value)
        onChange(segmentsOf(event.target.value))
      }}
    />
  )
}
