import { useEffect, useRef, useState, type Dispatch, type ReactElement, type SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp } from 'lucide-react'

import { addFilter, changeFilter, removeFilter, reorderFilters, type Filter, type RuleSet } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'
import { Switch } from '@/components/ui/switch.tsx'

import { ConfirmRemoval } from './confirm-removal.tsx'
import { describeSpec, type Wording } from './describe.ts'
import { FilterEditor } from './filter-editor.tsx'
import type { FoldersState } from './folders.ts'
import { MAX_FILTERS, filterErrorMessage } from './messages.ts'
import { readSpec } from './spec.ts'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

type Direction = 'up' | 'down'

/** `filters` in the order of `ids`; one not among them (added meanwhile) stays at the end. */
function inOrder(filters: Filter[], ids: number[]): Filter[] {
  const rank = (filter: Filter): number => {
    const at = ids.indexOf(filter.id)
    return at === -1 ? ids.length : at
  }
  return [...filters].sort((a, b) => rank(a) - rank(b))
}

/** A rule set's filters, in the order they run: each can be moved, turned off, edited or removed. */
export function FilterList({
  ruleSet,
  filters,
  vocabulary,
  folders,
  setFilters,
}: {
  ruleSet: RuleSet
  filters: Filter[]
  vocabulary: Vocabulary
  folders: FoldersState
  setFilters: Dispatch<SetStateAction<Filter[]>>
}): ReactElement {
  const { t, i18n } = useTranslation(FILTERS_NS)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** A reorder on its way: another waits for it, without disabling (and blurring) the buttons. */
  const moving = useRef(false)
  const addTrigger = useRef<HTMLButtonElement>(null)
  /** The move buttons, by `<filter id>-<up|down>`, so focus follows a moved filter. */
  const moveButtons = useRef(new Map<string, HTMLButtonElement>())
  /** The filter just moved, whose button takes focus once the list shows the move. */
  const focusAfterMove = useRef<{ id: number; direction: Direction } | null>(null)
  const wording: Wording = { t, language: i18n.language, vocabulary }
  const full = filters.length >= MAX_FILTERS

  useEffect(() => {
    if (focusAfterMove.current === null) {
      return
    }
    const { id, direction } = focusAfterMove.current
    // Once: a later change to the list leaves focus where the user put it.
    focusAfterMove.current = null
    const other = direction === 'up' ? 'down' : 'up'
    const preferred = moveButtons.current.get(`${String(id)}-${direction}`)
    const target = preferred?.disabled === false ? preferred : moveButtons.current.get(`${String(id)}-${other}`)
    target?.focus()
  }, [filters])

  async function move(index: number, direction: Direction): Promise<void> {
    const to = direction === 'up' ? index - 1 : index + 1
    const moved = filters[index]
    if (moving.current || moved === undefined || to < 0 || to >= filters.length) {
      return
    }
    const previous = filters.map((filter) => filter.id)
    const next = [...filters]
    next.splice(index, 1)
    next.splice(to, 0, moved)
    const ids = next.map((filter) => filter.id)
    setError(null)
    moving.current = true
    focusAfterMove.current = { id: moved.id, direction }
    setFilters((current) => inOrder(current, ids))
    try {
      await reorderFilters(ruleSet.id, ids)
    } catch (failure) {
      // Only the order goes back: a filter changed meanwhile keeps its change.
      setFilters((current) => inOrder(current, previous))
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
    } finally {
      moving.current = false
    }
  }

  return (
    <section aria-labelledby="filters-title" className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id="filters-title" className="text-lg font-medium">
          {t('filters.filter.title')}
        </h2>
        <p className="text-sm text-muted-foreground">{t('filters.filter.orderHint')}</p>
      </div>
      {filters.length === 0 && !adding && (
        <p className="text-sm text-muted-foreground">{t('filters.filter.none')}</p>
      )}
      {filters.length > 0 && (
        <ol aria-labelledby="filters-title" className="flex flex-col divide-y rounded-lg border">
          {filters.map((filter, index) => (
            <FilterRow
              key={filter.id}
              filter={filter}
              index={index}
              last={index === filters.length - 1}
              wording={wording}
              folders={folders}
              moveButtonRef={(direction, button) => {
                const key = `${String(filter.id)}-${direction}`
                if (button === null) {
                  moveButtons.current.delete(key)
                } else {
                  moveButtons.current.set(key, button)
                }
              }}
              onMove={(direction) => void move(index, direction)}
              onChanged={(changed) => {
                setFilters((current) => current.map((item) => (item.id === changed.id ? changed : item)))
              }}
              onRemoved={() => {
                setFilters((current) => current.filter((item) => item.id !== filter.id))
                requestAnimationFrame(() => addTrigger.current?.focus())
              }}
            />
          ))}
        </ol>
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {adding ? (
        <FilterEditor
          vocabulary={vocabulary}
          folders={folders}
          save={async (fields) => {
            const created = await addFilter(ruleSet.id, fields)
            setFilters((current) => [...current, created])
            setAdding(false)
            requestAnimationFrame(() => addTrigger.current?.focus())
          }}
          onCancel={() => {
            setAdding(false)
            requestAnimationFrame(() => addTrigger.current?.focus())
          }}
        />
      ) : (
        <div className="flex flex-col items-start gap-1">
          <Button
            ref={addTrigger}
            variant="outline"
            disabled={full}
            aria-describedby={full ? 'add-filter-full' : undefined}
            onClick={() => {
              setAdding(true)
            }}
          >
            {t('filters.editor.add')}
          </Button>
          {full && (
            <p id="add-filter-full" className="text-sm text-muted-foreground">
              {t('filters.editor.full', { max: MAX_FILTERS })}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function FilterRow({
  filter,
  index,
  last,
  wording,
  folders,
  moveButtonRef,
  onMove,
  onChanged,
  onRemoved,
}: {
  filter: Filter
  index: number
  last: boolean
  wording: Wording
  folders: FoldersState
  moveButtonRef: (direction: Direction, button: HTMLButtonElement | null) => void
  onMove: (direction: Direction) => void
  onChanged: (filter: Filter) => void
  onRemoved: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const [mode, setMode] = useState<'idle' | 'editing' | 'confirming-removal'>('idle')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const editTrigger = useRef<HTMLButtonElement>(null)
  const removeTrigger = useRef<HTMLButtonElement>(null)

  function backToIdle(trigger: typeof editTrigger): void {
    setMode('idle')
    setError(null)
    requestAnimationFrame(() => trigger.current?.focus())
  }

  async function toggle(enabled: boolean): Promise<void> {
    if (pending) {
      return
    }
    setPending(true)
    setError(null)
    try {
      onChanged(await changeFilter(filter.id, { enabled }))
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
    } finally {
      setPending(false)
    }
  }

  async function remove(): Promise<void> {
    setPending(true)
    setError(null)
    try {
      await removeFilter(filter.id)
      onRemoved()
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.actionFailed')))
      setPending(false)
    }
  }

  if (mode === 'editing') {
    return (
      <li className="p-3">
        <FilterEditor
          filter={filter}
          vocabulary={wording.vocabulary}
          folders={folders}
          save={async (fields) => {
            onChanged(await changeFilter(filter.id, fields))
            backToIdle(editTrigger)
          }}
          onCancel={() => {
            backToIdle(editTrigger)
          }}
        />
      </li>
    )
  }

  let summary: string
  try {
    summary = describeSpec(wording, readSpec(filter.spec))
  } catch {
    summary = t('filters.filter.unreadable')
  }
  return (
    <li className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span aria-hidden="true" className="w-6 text-sm text-muted-foreground tabular-nums">
          {index + 1}.
        </span>
        <div className={`flex min-w-0 flex-1 flex-col ${filter.enabled ? '' : 'opacity-60'}`}>
          <span className="font-medium">{filter.name}</span>
          <span className="text-sm break-words text-muted-foreground">{summary}</span>
        </div>
        <span className="flex items-center gap-1">
          <Switch
            checked={filter.enabled}
            // Not `disabled`: that would take focus from the switch just pressed.
            aria-disabled={pending}
            className="mr-2 aria-disabled:opacity-50"
            aria-label={t('filters.filter.enable', { name: filter.name })}
            onCheckedChange={(next) => void toggle(next)}
          />
          <Button
            ref={(button) => {
              moveButtonRef('up', button)
            }}
            variant="ghost"
            size="icon-sm"
            disabled={index === 0}
            aria-label={t('filters.filter.moveUp', { name: filter.name })}
            onClick={() => {
              onMove('up')
            }}
          >
            <ArrowUp aria-hidden="true" />
          </Button>
          <Button
            ref={(button) => {
              moveButtonRef('down', button)
            }}
            variant="ghost"
            size="icon-sm"
            disabled={last}
            aria-label={t('filters.filter.moveDown', { name: filter.name })}
            onClick={() => {
              onMove('down')
            }}
          >
            <ArrowDown aria-hidden="true" />
          </Button>
          {mode === 'idle' && (
            <>
              <Button
                ref={editTrigger}
                variant="outline"
                size="sm"
                aria-label={t('filters.filter.edit', { name: filter.name })}
                onClick={() => {
                  setError(null)
                  setMode('editing')
                }}
              >
                {t('filters.edit')}
              </Button>
              <Button
                ref={removeTrigger}
                variant="outline"
                size="sm"
                aria-label={t('filters.filter.remove', { name: filter.name })}
                onClick={() => {
                  setError(null)
                  setMode('confirming-removal')
                }}
              >
                {t('filters.remove')}
              </Button>
            </>
          )}
        </span>
      </div>
      {mode === 'confirming-removal' && (
        <ConfirmRemoval
          question={t('filters.filter.confirmRemoval', { name: filter.name })}
          pending={pending}
          onConfirm={() => void remove()}
          onCancel={() => {
            backToIdle(removeTrigger)
          }}
        />
      )}
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  )
}
