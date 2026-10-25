import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { Filter, FilterFields } from '@/api/filters.ts'
import schema from '@/test/filter-schema.json'

import { FilterEditor } from './filter-editor.tsx'
import type { FoldersState } from './folders.ts'
import { readVocabulary } from './vocabulary.ts'

const vocabulary = readVocabulary(schema)
const folders: FoldersState = { kind: 'loaded', paths: [['INBOX'], ['Archive'], ['Trash']] }

function renderEditor(spec: Record<string, unknown>): ReturnType<typeof vi.fn> {
  const save = vi.fn((fields: FilterFields) => Promise.resolve(fields))
  const filter: Filter = { id: 1, rule_set_id: 1, position: 0, name: 'Tidy', enabled: true, spec }
  render(<FilterEditor filter={filter} vocabulary={vocabulary} folders={folders} save={save} onCancel={vi.fn()} />)
  return save
}

async function saved(save: ReturnType<typeof vi.fn>): Promise<unknown> {
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await vi.waitFor(() => {
    expect(save).toHaveBeenCalled()
  })
  return (save.mock.lastCall?.[0] as FilterFields).spec
}

describe('the filter tree editor', () => {
  it('builds nested and/or/not groups, numbering conditions by their place', async () => {
    const save = renderEditor({
      criterion: { or: [{ type: 'from_contains', args: { addr: 'a@' } }, { type: 'is_read' }] },
      action: { type: 'star' },
    })

    const second = screen.getByRole('group', { name: 'Condition 2' })
    await userEvent.selectOptions(within(second).getByLabelText('Type of Condition 2'), 'Not this')
    const negated = screen.getByRole('group', { name: 'Condition 2.1' })
    await userEvent.selectOptions(within(negated).getByLabelText('Type of Condition 2.1'), 'All of these')
    await userEvent.click(screen.getByRole('button', { name: 'Add a condition to Condition 2.1' }))
    const added = screen.getByRole('group', { name: 'Condition 2.1.2' })
    await userEvent.selectOptions(within(added).getByLabelText('Type of Condition 2.1.2'), 'Has flag')
    await userEvent.selectOptions(within(added).getByLabelText('Flag'), 'Starred')

    // The first condition keeps its address when only its type changes.
    const first = screen.getByRole('group', { name: 'Condition 1' })
    await userEvent.selectOptions(within(first).getByLabelText('Type of Condition 1'), 'From is')
    expect(within(first).getByLabelText('Address')).toHaveValue('a@')

    expect(await saved(save)).toEqual({
      criterion: {
        or: [
          { type: 'from_is', args: { addr: 'a@' } },
          { not: { and: [{ type: 'is_read' }, { type: 'has_flag', args: { flag: 'FLAGGED' } }] } },
        ],
      },
      action: { type: 'star' },
    })
  })

  it('removes a condition, but never the last one of a group', async () => {
    const save = renderEditor({
      criterion: { and: [{ type: 'is_read' }, { type: 'is_starred' }] },
      action: { type: 'star' },
    })

    await userEvent.click(screen.getByRole('button', { name: 'Remove Condition 1' }))
    expect(screen.queryByRole('button', { name: /^Remove Condition/ })).not.toBeInTheDocument()
    expect(await saved(save)).toEqual({ criterion: { and: [{ type: 'is_starred' }] }, action: { type: 'star' } })
  })

  it('edits each kind of argument the schema lists', async () => {
    const save = renderEditor({
      criterion: {
        and: [
          { type: 'to_contains', args: { addr: 'me@' } },
          { type: 'older_than_days', args: { days: 30 } },
          { type: 'older_than', args: { cutoff_date: '2026-01-01' } },
        ],
      },
      action: { seq: [{ type: 'trash' }, { type: 'mark_as_read' }] },
    })

    const to = screen.getByRole('group', { name: 'Condition 1' })
    await userEvent.click(within(to).getByLabelText('Include Cc'))
    const days = within(screen.getByRole('group', { name: 'Condition 2' })).getByLabelText('Days')
    expect(days).toHaveAttribute('min', '0')
    await userEvent.clear(days)
    await userEvent.type(days, '90')
    const date = within(screen.getByRole('group', { name: 'Condition 3' })).getByLabelText('Before')
    expect(date).toHaveAttribute('type', 'date')

    const trash = screen.getByRole('group', { name: 'Action 1' })
    const folder = within(trash).getByLabelText('Folder')
    expect(folder).toHaveValue('')
    expect(within(folder).getByRole('option', { name: "The account's default" })).toBeInTheDocument()
    await userEvent.selectOptions(folder, 'Trash')
    await userEvent.click(screen.getByRole('button', { name: 'Remove Action 2' }))
    expect(screen.getByRole('button', { name: 'Remove Action 1' })).toBeDisabled()

    expect(await saved(save)).toEqual({
      criterion: {
        and: [
          { type: 'to_contains', args: { addr: 'me@', incl_cc: true } },
          { type: 'older_than_days', args: { days: 90 } },
          { type: 'older_than', args: { cutoff_date: '2026-01-01' } },
        ],
      },
      action: { type: 'trash', args: { folder: ['Trash'] } },
    })
  })

  it('opens a spec the tree cannot show as JSON', () => {
    renderEditor({ criterion: { nope: true }, action: { type: 'star' } })

    expect(screen.getByRole('tab', { name: 'JSON' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toContainElement(screen.getByLabelText('Filter spec (JSON)'))
  })
})
