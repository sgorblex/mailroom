import { useContext, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/components/ui/input.tsx'

import { argLabel, choiceLabel } from './describe.ts'
import { FolderPicker, selectClass } from './folder-picker.tsx'
import { useFolders, type FoldersState } from './folders.ts'
import { todayIso, type Args } from './spec.ts'
import { TargetAccountsContext } from './targets.ts'
import { initialArg, takesAccount, type ArgDef, type LeafDef } from './vocabulary.ts'
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
  if (takesAccount(def)) {
    return <TargetFields idPrefix={idPrefix} def={def} args={args} onChange={onChange} />
  }
  return <Fields idPrefix={idPrefix} def={def} args={args} folders={folders} onChange={onChange} />
}

/** The fields of a leaf that takes another account: its folders are that account's. */
function TargetFields({
  idPrefix,
  def,
  args,
  onChange,
}: {
  idPrefix: string
  def: LeafDef
  args: Args
  onChange: (args: Args) => void
}): ReactElement {
  const targets = useContext(TargetAccountsContext)
  const account = def.args.find((arg) => arg.field.kind === 'account')
  const chosen = account === undefined ? undefined : args[account.name]
  // A target no longer the user's has no folders to list.
  const listed = targets.some((target) => target.id === chosen)
  const folders = useFolders(listed && typeof chosen === 'number' ? chosen : null)
  return <Fields idPrefix={idPrefix} def={def} args={args} folders={folders} onChange={onChange} />
}

function Fields({
  idPrefix,
  def,
  args,
  folders,
  onChange,
}: {
  idPrefix: string
  def: LeafDef
  args: Args
  folders: FoldersState
  onChange: (args: Args) => void
}): ReactElement {
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
            const next = { ...args, [arg.name]: value }
            if (arg.field.kind === 'account') {
              // A folder chosen in the account before is not one of this one's.
              for (const other of def.args) {
                if (other.field.kind === 'folder') {
                  next[other.name] = initialArg(other, todayIso())
                }
              }
            }
            onChange(next)
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
    case 'account':
      input = <AccountSelect id={id} value={value} onChange={onChange} />
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

/** Picks another of the user's accounts; a stored one no longer among them still shows, as removed. */
function AccountSelect({
  id,
  value,
  onChange,
}: {
  id: string
  value: unknown
  onChange: (value: unknown) => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const targets = useContext(TargetAccountsContext)
  const chosen = typeof value === 'number' ? value : null
  const known = chosen === null || targets.some((account) => account.id === chosen)
  return (
    <select
      id={id}
      required
      className={selectClass}
      value={chosen === null ? '' : String(chosen)}
      onChange={(event) => {
        onChange(event.target.value === '' ? undefined : Number(event.target.value))
      }}
    >
      <option value="">{t('filters.chooseAccount')}</option>
      {!known && <option value={String(chosen)}>{t('accounts.unknown', { ns: 'common' })}</option>}
      {targets.map((account) => (
        <option key={account.id} value={String(account.id)}>
          {account.display_name}
        </option>
      ))}
    </select>
  )
}
