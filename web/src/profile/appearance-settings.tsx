import { useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { ACCENTS, DENSITIES, READING_PANES, TEXT_SIZES, THEMES, type Appearance } from '@/api/auth.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { useAppearance } from '@/auth/auth-context.ts'
import { lookOf, setLook, type Look } from '@/theme/look.ts'

type Outcome = { kind: 'idle' } | { kind: 'saved' } | { kind: 'failed'; message: string }

/** What this section chooses: the look of the whole page, the density of the mail lists and where a message opens. */
type Shown = Look & Pick<Appearance, 'density' | 'reading_pane'>

const shownOf = (appearance: Appearance): Shown => ({
  ...lookOf(appearance),
  density: appearance.density,
  reading_pane: appearance.reading_pane,
})

/**
 * The user's theme, accent, text size, density and reading pane, the same on every device. A
 * choice shows at once and is saved; when the save fails the saved look comes back and the
 * section says so.
 */
export function AppearanceSettings(): ReactElement {
  const { t } = useTranslation()
  const { appearance, save } = useAppearance()
  const [shown, setShown] = useState<Shown>(() => shownOf(appearance))
  const [saved, setSaved] = useState<Appearance>(appearance)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' })
  // Choices whose save has not answered yet, and the newest choice: only its answer counts.
  const [pending, setPending] = useState(0)
  const newest = useRef(0)
  const latest = useRef(appearance)
  if (pending === 0 && saved !== appearance) {
    // The saved look changed (a save answered, or the session was read again): show it. Not while a
    // choice is still being saved, which is what the user wants to see meanwhile.
    setSaved(appearance)
    setShown(shownOf(appearance))
  }
  useEffect(() => {
    latest.current = appearance
  }, [appearance])
  // LookSync puts the saved look on the page whenever it changes; while this section shows another
  // (a choice still being saved), that one wins.
  useEffect(() => {
    setLook(shown)
  }, [shown, appearance])

  async function choose(change: Partial<Shown>): Promise<void> {
    const request = ++newest.current
    setShown({ ...shown, ...change })
    setOutcome({ kind: 'idle' })
    setPending((count) => count + 1)
    try {
      await save(change)
      if (request === newest.current) setOutcome({ kind: 'saved' })
    } catch (failure) {
      if (request === newest.current) {
        setShown(shownOf(latest.current))
        setOutcome({ kind: 'failed', message: apiErrorMessage(failure, t('appearance.saveFailed')) })
      }
    } finally {
      setPending((count) => count - 1)
    }
  }

  return (
    <section aria-labelledby="appearance" className="flex flex-col gap-3">
      <h2 id="appearance" className="font-medium">
        {t('appearance.title')}
      </h2>
      <p id="appearance-intro" className="text-sm text-muted-foreground">
        {t('appearance.intro')}
      </p>
      <fieldset className="flex flex-col gap-1.5" aria-describedby="appearance-intro">
        <legend className="mb-1.5 text-sm font-medium">{t('appearance.theme')}</legend>
        {THEMES.map((theme) => (
          <label key={theme} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="appearance-theme"
              value={theme}
              checked={shown.theme === theme}
              onChange={() => void choose({ theme })}
            />
            {t(`appearance.themes.${theme}`)}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-1.5" aria-describedby="appearance-intro">
        <legend className="mb-1.5 text-sm font-medium">{t('appearance.accent')}</legend>
        <div className="flex flex-wrap gap-2">
          {ACCENTS.map((accent) => (
            <label key={accent} title={t(`appearance.accents.${accent}`)} className="relative">
              <input
                type="radio"
                name="appearance-accent"
                value={accent}
                aria-label={t(`appearance.accents.${accent}`)}
                className="peer sr-only"
                checked={shown.accent === accent}
                onChange={() => void choose({ accent })}
              />
              <span
                aria-hidden="true"
                data-accent={accent}
                className="block size-8 rounded-full border-2 border-transparent bg-primary ring-offset-2 ring-offset-background peer-checked:ring-2 peer-checked:ring-foreground peer-focus-visible:outline-2 peer-focus-visible:outline-offset-4 peer-focus-visible:outline-ring"
              />
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="flex flex-col gap-1.5" aria-describedby="appearance-intro">
        <legend className="mb-1.5 text-sm font-medium">{t('appearance.textSize')}</legend>
        {TEXT_SIZES.map((size) => (
          <label key={size} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="appearance-text-size"
              value={size}
              checked={shown.text_size === size}
              onChange={() => void choose({ text_size: size })}
            />
            {t(`appearance.textSizes.${size}`)}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-sm font-medium">{t('appearance.density')}</legend>
        {DENSITIES.map((density) => (
          <label key={density} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="appearance-density"
              value={density}
              checked={shown.density === density}
              onChange={() => void choose({ density })}
            />
            {t(`appearance.densities.${density}`)}
          </label>
        ))}
      </fieldset>
      <fieldset className="flex flex-col gap-1.5" aria-describedby="appearance-reading-pane-phones">
        <legend className="mb-1.5 text-sm font-medium">{t('appearance.readingPane')}</legend>
        {READING_PANES.map((position) => (
          <label key={position} className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="appearance-reading-pane"
              value={position}
              checked={shown.reading_pane === position}
              onChange={() => void choose({ reading_pane: position })}
            />
            {t(`appearance.readingPanes.${position}`)}
          </label>
        ))}
        <p id="appearance-reading-pane-phones" className="text-xs text-muted-foreground">
          {t('appearance.readingPanePhones')}
        </p>
      </fieldset>
      {outcome.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {outcome.message}
        </p>
      )}
      {outcome.kind === 'saved' && (
        <p role="status" className="text-sm">
          {t('appearance.saved')}
        </p>
      )}
    </section>
  )
}
