import { useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/components/ui/input.tsx'

import { folderLabel } from './describe.ts'
import type { FoldersState } from './folders.ts'
import { FILTERS_NS } from './i18n.ts'

export const selectClass = 'h-9 w-full rounded-md border bg-transparent px-2 text-sm aria-invalid:border-destructive'

/**
 * Picks one of the account's folders. `null` is no folder: "choose one" unless `noneLabel` names
 * what none means (the trash action's default folder). When the folders cannot be listed, the
 * path is typed instead, its segments separated by `/`; so it is for a rule set for all accounts,
 * where an optional folder is the account's own by role unless a path is chosen, and a path warns
 * that an account without it fails the move.
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

  if (folders.kind === 'all-accounts') {
    return (
      <AllAccountsFolder
        // A required folder and an optional one start apart: by path, and by role unless one is set.
        key={noneLabel === undefined ? 'required' : 'optional'}
        id={id}
        value={chosen}
        onChange={onChange}
        optional={noneLabel !== undefined}
        describedBy={describedBy}
        invalid={invalid}
      />
    )
  }
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

/**
 * A folder for a rule set for all accounts: by role (an optional folder left out) or by a typed
 * path, with the warning that an account without that folder fails the move.
 */
function AllAccountsFolder({
  id,
  value,
  onChange,
  optional,
  describedBy,
  invalid,
}: {
  id: string
  value: readonly string[] | null
  onChange: (path: string[] | null) => void
  optional: boolean
  describedBy: string | undefined
  invalid: boolean
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const warningId = useId()
  const [byPath, setByPath] = useState(!optional || value !== null)
  const described = optional || describedBy === undefined ? warningId : `${describedBy} ${warningId}`
  return (
    <div className="flex flex-col gap-2">
      {optional && (
        <select
          id={id}
          aria-describedby={describedBy}
          className={selectClass}
          value={byPath ? 'path' : 'role'}
          onChange={(event) => {
            const path = event.target.value === 'path'
            setByPath(path)
            if (!path) {
              onChange(null)
            }
          }}
        >
          <option value="role">{t('filters.allAccounts.byRole')}</option>
          <option value="path">{t('filters.allAccounts.byPath')}</option>
        </select>
      )}
      {byPath && (
        <>
          <TypedFolder
            id={optional ? `${id}-path` : id}
            label={optional ? t('filters.allAccounts.path') : undefined}
            value={value}
            onChange={onChange}
            required
            describedBy={described}
            invalid={invalid}
          />
          <p id={warningId} className="text-sm text-muted-foreground">
            {t('filters.allAccounts.pathWarning')}
          </p>
        </>
      )}
    </div>
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
  label,
  value,
  onChange,
  required,
  describedBy,
  invalid,
}: {
  id: string
  /** Its own label, when no `<label>` names `id`. */
  label?: string
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
      aria-label={label}
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
