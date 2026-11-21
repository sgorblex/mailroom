import { describe, expect, it } from 'vitest'

import {
  inlinePart,
  itemName,
  plainToHtml,
  reconcileInline,
  reconcileTray,
  shownImages,
  trayPart,
  uploadProblem,
  type InlineItem,
  type TrayFile,
  type TrayItem,
} from './compose-form.ts'

const file = (id: string, name: string): TrayFile => ({ kind: 'file', id, file: new File(['x'], name) })
const part = (name: string, number: string) => ({ name, type: 'application/pdf', size: 1, part: number })
const named = (tray: TrayItem[]): string[] => tray.map(itemName)
const numbers = (tray: TrayItem[]): string[] => tray.map((item) => (item.kind === 'part' ? item.part : item.id))

describe('the tray after a save', () => {
  it('turns uploads into parts of the saved version', () => {
    const tray = [file('a', 'a.pdf')]
    expect(named(reconcileTray(tray, tray, 'r2', [part('a.pdf', '2')]))).toEqual(['a.pdf'])
    expect(reconcileTray(tray, tray, 'r2', [part('a.pdf', '2')])[0]).toMatchObject({ kind: 'part', ref: 'r2', part: '2' })
  })

  it('drops the file removed during its upload, of two with one name', () => {
    const [one, two] = [file('a', 'scan.pdf'), file('b', 'scan.pdf')]
    const saved = [one, two]
    const parts = [part('scan.pdf', '2'), part('scan.pdf', '3')]
    expect(numbers(reconcileTray([one], saved, 'r2', parts))).toEqual(['2'])
    expect(numbers(reconcileTray([two], saved, 'r2', parts))).toEqual(['3'])
  })

  it('drops a removed file whose name the server changed', () => {
    const removed = file('a', `${'x'.repeat(250)}.pdf`)
    const stays = file('b', 'b.pdf')
    const tray = reconcileTray([stays], [removed, stays], 'r2', [part(`${'x'.repeat(196)}.pdf`, '2'), part('b.pdf', '3')])
    expect(numbers(tray)).toEqual(['3'])
  })

  it('matches kept parts first, as the server lists them', () => {
    const kept = trayPart({ ...part('kept.pdf', '2'), ref: 'r1' })
    const added = file('a', 'new.pdf')
    const parts = [part('kept.pdf', '2'), part('new.pdf', '3')]
    expect(numbers(reconcileTray([kept], [added, kept], 'r2', parts))).toEqual(['2'])
  })

  it('goes by name when the parts are not the ones sent', () => {
    const kept = trayPart({ ...part('a.pdf', '2'), ref: 'r1' })
    const removed = trayPart({ ...part('b.pdf', '3'), ref: 'r1' })
    const tray = reconcileTray([kept], [kept, removed], 'r2', [part('b.pdf', '2')])
    expect(tray).toEqual([])
  })

  it('leaves what was added meanwhile for the next save', () => {
    const later = file('later', 'later.pdf')
    expect(named(reconcileTray([later], [], 'r2', []))).toEqual(['later.pdf'])
  })
})

describe('inline images', () => {
  const image = (contentId: string): InlineItem => ({ kind: 'file', id: contentId, file: new File(['x'], 'a.png'), contentId })

  it('are the ones the HTML shows, and none in plain text', () => {
    const inline = [image('a@m'), image('b@m')]
    expect(shownImages('<p><img src="cid:b@m" alt="a.png"></p>', inline)).toEqual([inline[1]])
    expect(shownImages(null, inline)).toEqual([])
  })

  it('become the parts they were saved as, matched by Content-ID', () => {
    const kept = inlinePart({ ref: 'r1', part: '3', name: 'k.png', type: 'image/png', size: 1, content_id: 'k@m' })
    const after = reconcileInline([image('a@m'), kept, image('later@m')], 'r2', [
      { part: '2', name: 'a.png', type: 'image/png', size: 1, content_id: 'a@m' },
      { part: '3', name: 'k.png', type: 'image/png', size: 1, content_id: 'k@m' },
    ], null)
    expect(after.map((item) => (item.kind === 'part' ? `${item.ref}:${item.part}` : item.id))).toEqual(['r2:2', 'r2:3', 'later@m'])
    expect(after[0]).toMatchObject({ contentId: 'a@m' })
  })

  it('drop the parts of the version a save replaced that it did not show, not the forwarded ones', () => {
    const deleted = inlinePart({ ref: 'r1', part: '3', name: 'd.png', type: 'image/png', size: 1, content_id: 'd@m' })
    const forwarded = inlinePart({ ref: 'orig', part: '2', name: 'f.png', type: 'image/png', size: 1, content_id: 'f@m' })
    const after = reconcileInline([deleted, forwarded], 'r2', [], 'orig')
    expect(after).toEqual([forwarded])
  })

  it('count towards the upload limits', () => {
    const big = new File([new Uint8Array(2 * 2 ** 20)], 'big.png')
    expect(uploadProblem([image('a@m')], [big], 1)).toBe('size')
    expect(uploadProblem(Array.from({ length: 10 }, (_, at) => image(String(at))), [big], 10)).toBe('count')
    expect(uploadProblem([], [big], 10)).toBeNull()
  })
})

describe('plain text as HTML', () => {
  it('is a paragraph a line, escaped', () => {
    expect(plainToHtml('a <b>\n\nc')).toBe('<p>a &lt;b&gt;</p><p></p><p>c</p>')
  })
})
