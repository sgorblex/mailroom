import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmRemoval } from './confirm-removal.tsx'

describe('ConfirmRemoval', () => {
  it('focuses Cancel, so Enter backs out instead of removing', async () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(
      <ConfirmRemoval
        question="Remove it?"
        confirmLabel="Remove"
        cancelLabel="Cancel"
        pending={false}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    )
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onCancel).toHaveBeenCalledOnce()
    expect(onConfirm).not.toHaveBeenCalled()
  })
})
