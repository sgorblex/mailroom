import { useId, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'

import { typeLabel } from './describe.ts'
import { selectClass } from './folder-picker.tsx'
import type { FoldersState } from './folders.ts'
import { LeafFields } from './leaf-fields.tsx'
import {
  changeKind,
  freshLeaf,
  kindOf,
  newActionLeaf,
  newCriterionLeaf,
  type CriterionKind,
  type CriterionNode,
  type Leaf,
  type SpecModel,
} from './spec.ts'
import type { LeafDef, Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

/** The structured editor of a filter spec: the criterion as a tree, then the actions in order. */
export function SpecTree({
  model,
  vocabulary,
  folders,
  onChange,
}: {
  model: SpecModel
  vocabulary: Vocabulary
  folders: FoldersState
  onChange: (model: SpecModel) => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-medium">{t('filters.tree.when')}</h4>
        <CriterionEditor
          node={model.criterion}
          path={[]}
          vocabulary={vocabulary}
          folders={folders}
          onChange={(criterion) => {
            onChange({ ...model, criterion })
          }}
        />
      </div>
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-medium">{t('filters.tree.then')}</h4>
        <ActionList
          actions={model.actions}
          vocabulary={vocabulary}
          folders={folders}
          onChange={(actions) => {
            onChange({ ...model, actions })
          }}
        />
      </div>
    </div>
  )
}

function LeafOptions({ defs, current }: { defs: LeafDef[]; current: string | null }): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const known = current === null || defs.some((def) => def.type === current)
  return (
    <>
      {!known && <option value={`leaf:${current}`}>{current}</option>}
      {defs.map((def) => (
        <option key={def.type} value={`leaf:${def.type}`}>
          {typeLabel(t, def.type)}
        </option>
      ))}
    </>
  )
}

function CriterionEditor({
  node,
  path,
  vocabulary,
  folders,
  onChange,
  onRemove,
}: {
  node: CriterionNode
  path: number[]
  vocabulary: Vocabulary
  folders: FoldersState
  onChange: (node: CriterionNode) => void
  onRemove?: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const idPrefix = useId()
  const name =
    path.length === 0
      ? t('filters.tree.root')
      : t('filters.tree.condition', { n: path.map((index) => String(index + 1)).join('.') })
  const nested = path.length > 0

  return (
    <div
      role="group"
      aria-label={name}
      className={`flex flex-col gap-3 rounded-md border p-3 ${nested ? 'bg-muted/40' : ''}`}
    >
      <div className="flex items-center gap-2">
        <select
          aria-label={t('filters.tree.typeOf', { name })}
          className={`${selectClass} max-w-72`}
          value={kindOf(node)}
          onChange={(event) => {
            onChange(changeKind(vocabulary, node, event.target.value as CriterionKind))
          }}
        >
          <optgroup label={t('filters.tree.groups')}>
            <option value="and">{t('filters.tree.and')}</option>
            <option value="or">{t('filters.tree.or')}</option>
            <option value="not">{t('filters.tree.not')}</option>
          </optgroup>
          <optgroup label={t('filters.tree.conditions')}>
            <LeafOptions defs={vocabulary.criteria} current={node.kind === 'leaf' ? node.type : null} />
          </optgroup>
        </select>
        {onRemove !== undefined && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="ml-auto"
            aria-label={t('filters.tree.remove', { name })}
            onClick={onRemove}
          >
            <X aria-hidden="true" />
          </Button>
        )}
      </div>
      {node.kind === 'leaf' && (
        <LeafFields
          idPrefix={idPrefix}
          defs={vocabulary.criteria}
          type={node.type}
          args={node.args}
          folders={folders}
          onChange={(args) => {
            onChange({ ...node, args })
          }}
        />
      )}
      {node.kind === 'not' && (
        <CriterionEditor
          node={node.child}
          path={[...path, 0]}
          vocabulary={vocabulary}
          folders={folders}
          onChange={(child) => {
            onChange({ ...node, child })
          }}
        />
      )}
      {(node.kind === 'and' || node.kind === 'or') && (
        <>
          {node.children.map((child, index) => (
            <div key={child.key} className="flex flex-col gap-3">
              {index > 0 && (
                <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {t(`filters.summary.${node.kind}`)}
                </span>
              )}
              <CriterionEditor
                node={child}
                path={[...path, index]}
                vocabulary={vocabulary}
                folders={folders}
                onChange={(changed) => {
                  onChange({ ...node, children: node.children.map((item, at) => (at === index ? changed : item)) })
                }}
                onRemove={
                  node.children.length > 1
                    ? () => {
                        onChange({ ...node, children: node.children.filter((_, at) => at !== index) })
                      }
                    : undefined
                }
              />
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            aria-label={t('filters.tree.addTo', { name })}
            onClick={() => {
              onChange({ ...node, children: [...node.children, newCriterionLeaf(vocabulary)] })
            }}
          >
            <Plus aria-hidden="true" />
            {t('filters.tree.add')}
          </Button>
        </>
      )}
    </div>
  )
}

function ActionList({
  actions,
  vocabulary,
  folders,
  onChange,
}: {
  actions: Leaf[]
  vocabulary: Vocabulary
  folders: FoldersState
  onChange: (actions: Leaf[]) => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const idPrefix = useId()

  function move(from: number, to: number): void {
    const next = [...actions]
    const [moved] = next.splice(from, 1)
    if (moved !== undefined) {
      next.splice(to, 0, moved)
      onChange(next)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ol aria-label={t('filters.tree.actions')} className="flex flex-col gap-3">
        {actions.map((action, index) => {
          const name = t('filters.tree.action', { n: index + 1 })
          return (
            <li
              key={action.key}
              role="group"
              aria-label={name}
              className="flex flex-col gap-3 rounded-md border p-3"
            >
              <div className="flex items-center gap-2">
                <span aria-hidden="true" className="w-5 text-sm text-muted-foreground tabular-nums">
                  {index + 1}.
                </span>
                <select
                  aria-label={t('filters.tree.typeOf', { name })}
                  className={`${selectClass} max-w-72`}
                  value={`leaf:${action.type}`}
                  onChange={(event) => {
                    const type = event.target.value.slice('leaf:'.length)
                    const changed = { ...freshLeaf(vocabulary.actions, type, action.args), key: action.key }
                    onChange(actions.map((item, at) => (at === index ? changed : item)))
                  }}
                >
                  <LeafOptions defs={vocabulary.actions} current={action.type} />
                </select>
                <span className="ml-auto flex gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={index === 0}
                    aria-label={t('filters.tree.moveUp', { name })}
                    onClick={() => {
                      move(index, index - 1)
                    }}
                  >
                    <ArrowUp aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={index === actions.length - 1}
                    aria-label={t('filters.tree.moveDown', { name })}
                    onClick={() => {
                      move(index, index + 1)
                    }}
                  >
                    <ArrowDown aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    disabled={actions.length === 1}
                    aria-label={t('filters.tree.remove', { name })}
                    onClick={() => {
                      onChange(actions.filter((_, at) => at !== index))
                    }}
                  >
                    <X aria-hidden="true" />
                  </Button>
                </span>
              </div>
              <LeafFields
                idPrefix={`${idPrefix}-${action.key}`}
                defs={vocabulary.actions}
                type={action.type}
                args={action.args}
                folders={folders}
                onChange={(args) => {
                  onChange(actions.map((item, at) => (at === index ? { ...action, args } : item)))
                }}
              />
            </li>
          )
        })}
      </ol>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-start"
        onClick={() => {
          onChange([...actions, newActionLeaf(vocabulary)])
        }}
      >
        <Plus aria-hidden="true" />
        {t('filters.tree.addAction')}
      </Button>
    </div>
  )
}
