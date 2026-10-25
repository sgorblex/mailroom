import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/components/ui/input.tsx'

import { argLabel, choiceLabel } from './describe.ts'
import { FolderPicker, selectClass } from './folder-picker.tsx'
import type { FoldersState } from './folders.ts'
import type { Args } from './spec.ts'
import type { ArgDef, LeafDef } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

/** The argument fields of one leaf, as its definition in the schema lists them. */
export function LeafFields({
  idPrefix,
  defs,
  type,
  args,
  folders,
  onChange,
}: {
  idPrefix: string
  defs: LeafDef[]
  type: string
  args: Args
  folders: FoldersState
  onChange: (args: Args) => void
}): ReactElement | null {
  const { t } = useTranslation(FILTERS_NS)
  const def = defs.find((candidate) => candidate.type === type)
  if (def === undefined) {
    return <p className="text-sm text-destructive">{t('filters.tree.unknownType', { type })}</p>
  }
  if (def.args.length === 0) {
    return null
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {def.args.map((arg) => (
        <ArgField
          key={arg.name}
          id={`${idPrefix}-${arg.name}`}
          arg={arg}
          value={args[arg.name]}
          folders={folders}
          onChange={(value) => {
            onChange({ ...args, [arg.name]: value })
          }}
        />
      ))}
    </div>
  )
}

function ArgField({
  id,
  arg,
  value,
  folders,
  onChange,
}: {
  id: string
  arg: ArgDef
  value: unknown
  folders: FoldersState
  onChange: (value: unknown) => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const label = argLabel(t, arg.name)
  const field = arg.field

  if (field.kind === 'boolean') {
    return (
      <label className="flex items-center gap-2 self-end text-sm">
        <input
          id={id}
          type="checkbox"
          checked={value === true}
          onChange={(event) => {
            onChange(event.target.checked)
          }}
        />
        {label}
      </label>
    )
  }

  let input: ReactElement
  switch (field.kind) {
    case 'unknown':
      return (
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">{label}</span>
          <p className="text-sm text-muted-foreground">{t('filters.tree.unknownArg')}</p>
        </div>
      )
    case 'folder':
      input = (
        <FolderPicker
          id={id}
          folders={folders}
          value={Array.isArray(value) ? value.map(String) : null}
          noneLabel={field.optional ? t('filters.folderDefault') : undefined}
          onChange={(path) => {
            onChange(path ?? (field.optional ? undefined : []))
          }}
        />
      )
      break
    case 'choice':
      input = (
        <select
          id={id}
          className={selectClass}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => {
            onChange(event.target.value)
          }}
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {choiceLabel(t, option)}
            </option>
          ))}
        </select>
      )
      break
    case 'integer':
      input = (
        <Input
          id={id}
          type="number"
          required={arg.required}
          min={field.minimum}
          max={field.maximum}
          step={1}
          value={typeof value === 'number' ? String(value) : ''}
          onChange={(event) => {
            onChange(event.target.value === '' ? undefined : Number(event.target.value))
          }}
        />
      )
      break
    case 'date':
      input = (
        <Input
          id={id}
          type="date"
          required={arg.required}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => {
            onChange(event.target.value === '' ? undefined : event.target.value)
          }}
        />
      )
      break
    case 'text':
      input = (
        <Input
          id={id}
          required={arg.required}
          autoCapitalize="none"
          spellCheck={false}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => {
            onChange(event.target.value === '' && !arg.required ? undefined : event.target.value)
          }}
        />
      )
      break
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {input}
    </div>
  )
}
