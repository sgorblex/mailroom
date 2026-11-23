import axe from 'axe-core'
import { expect } from 'vitest'

interface Options {
  /**
   * Whether all content must sit in a landmark (axe's `region`): only for a whole page, inside the
   * app shell; a screen rendered on its own has no shell around it.
   */
  page?: boolean
}

/**
 * Fails with axe's findings in `container`, one line per rule and element. Contrast is left out:
 * jsdom loads no stylesheet and lays nothing out, and `src/theme/contrast.node.test.ts` checks the
 * palette instead. Frames are left out too: the only ones hold a mail's own HTML, which is the
 * sender's, and axe cannot reach into a jsdom frame (the iframe element itself is still checked).
 */
export async function expectNoAxeViolations(container: Element = document.body, { page = false }: Options = {}): Promise<void> {
  const results = await axe.run(container, {
    iframes: false,
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false }, region: { enabled: page } },
  })
  const found = results.violations.flatMap((violation) =>
    violation.nodes.map((node) => `${violation.id}: ${violation.help} — ${node.html}`),
  )
  expect(found).toEqual([])
}
