import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { YourData } from './your-data.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const AUDIT = '/api/v1/me/export/audit.csv'

function auditLink(): HTMLElement {
  return screen.getByRole('link', { name: 'Download activity (CSV)' })
}

function pick(label: string, day: string): void {
  fireEvent.change(screen.getByLabelText(label), { target: { value: day } })
}

describe('YourData', () => {
  it('links to both downloads', async () => {
    renderSignedIn(<YourData />, ALICE)
    const ruleSets = screen.getByRole('link', { name: 'Download rule sets (JSON)' })
    expect(ruleSets).toHaveAttribute('href', '/api/v1/me/export/rule-sets')
    expect(ruleSets).toHaveAttribute('download')
    expect(auditLink()).toHaveAttribute('href', AUDIT)
    expect(auditLink()).toHaveAttribute('download')
    await expectNoAxeViolations()
  })

  it('takes the days of the trail, either or both', () => {
    renderSignedIn(<YourData />, ALICE)
    pick('From', '2026-11-01')
    expect(auditLink()).toHaveAttribute('href', `${AUDIT}?since=2026-11-01`)
    pick('To', '2026-11-01')
    expect(auditLink()).toHaveAttribute('href', `${AUDIT}?since=2026-11-01&until=2026-11-01`)
    pick('From', '')
    expect(auditLink()).toHaveAttribute('href', `${AUDIT}?until=2026-11-01`)
  })

  it('downloads no trail of days in the wrong order', async () => {
    renderSignedIn(<YourData />, ALICE)
    pick('From', '2026-11-02')
    pick('To', '2026-11-01')
    expect(screen.getByRole('alert')).toHaveTextContent('The last day is before the first.')
    expect(screen.getByLabelText('To')).toHaveAccessibleDescription('The last day is before the first.')
    expect(screen.queryByRole('link', { name: /activity/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download activity (CSV)' })).toBeDisabled()
    await expectNoAxeViolations()
    pick('To', '2026-11-03')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(auditLink()).toBeInTheDocument()
  })

  it('leaves the rule sets out where the instance runs no filters', () => {
    renderSignedIn(<YourData />, ALICE, ['mail'])
    expect(screen.queryByRole('link', { name: /rule sets/ })).not.toBeInTheDocument()
    expect(auditLink()).toBeInTheDocument()
  })
})
