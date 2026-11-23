import { useEffect, useRef, useState, type MouseEvent, type RefObject } from 'react'

import { matchesMedia } from '@/lib/media-query.ts'

/**
 * How long a click or a link's hash keeps its section marked while the page scrolls there, when the
 * browser sends no `scrollend` sooner.
 */
const HOLD_MS = 1500

function isSection<Id extends string>(ids: readonly Id[], id: string): id is Id {
  return (ids as readonly string[]).includes(id)
}

/** The section the URL's hash names, if it is one of `ids`. */
function hashSection<Id extends string>(ids: readonly Id[]): Id | null {
  const id = window.location.hash.slice(1)
  return isSection(ids, id) ? id : null
}

/** Whether the page is scrolled down to its end (and can scroll at all). */
function atBottom(): boolean {
  return window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 1
}

/**
 * Holds the mark where a click or a hash put it until the scroll settles: `held` gets the release,
 * called on `scrollend`, after `HOLD_MS` at the latest, or by the next hold.
 */
function hold(held: RefObject<(() => void) | null>): void {
  letGo(held)
  const release = (): void => {
    window.clearTimeout(timer)
    window.removeEventListener('scrollend', release)
    if (held.current === release) {
      held.current = null
    }
  }
  const timer = window.setTimeout(release, HOLD_MS)
  window.addEventListener('scrollend', release)
  held.current = release
}

/** Ends the hold in `held`, if there is one. */
function letGo(held: RefObject<(() => void) | null>): void {
  held.current?.()
}

/**
 * Which of a page's sections (elements whose ids are `ids`, in page order) a nav marks: the one the
 * URL names on arrival (scrolled to), then the first one crossing the top third of the window as it
 * scrolls, the last one once the page is scrolled to its end (a short last section never reaches the
 * top), and the one a nav link was clicked for. A click or a hash holds its section until the scroll
 * it starts settles, so the sections passed on the way do not take the mark. Without
 * `IntersectionObserver` it follows the clicks and the hash only. Returns the marked id and the nav
 * links' click handler, which scrolls to the section, focuses it and keeps its hash in the URL.
 */
export function useSectionSpy<Id extends string>(
  ids: readonly Id[],
): [Id | undefined, (event: MouseEvent<HTMLAnchorElement>, id: Id) => void] {
  const [current, setCurrent] = useState<Id | undefined>(() => hashSection(ids) ?? ids[0])
  /** Releases the hold of a click or a hash; null when nothing holds the mark. */
  const held = useRef<(() => void) | null>(null)
  const key = ids.join(' ')

  useEffect(() => {
    const sections = key.split(' ') as Id[]
    function follow(): void {
      const id = hashSection(sections)
      if (id !== null) {
        hold(held)
        setCurrent(id)
        document.getElementById(id)?.scrollIntoView()
      }
    }
    follow()
    window.addEventListener('hashchange', follow)
    if (typeof IntersectionObserver !== 'function') {
      return () => {
        window.removeEventListener('hashchange', follow)
        letGo(held)
      }
    }
    const visible = new Set<string>()
    function mark(): void {
      if (held.current !== null) {
        return
      }
      const id = atBottom() ? sections.at(-1) : sections.find((section) => visible.has(section))
      if (id !== undefined) {
        setCurrent(id)
      }
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.add(entry.target.id)
          } else {
            visible.delete(entry.target.id)
          }
        }
        mark()
      },
      // A band across the top third of the window: the section crossing it is the one being read.
      { rootMargin: '0px 0px -66% 0px' },
    )
    for (const id of sections) {
      const element = document.getElementById(id)
      if (element !== null) {
        observer.observe(element)
      }
    }
    // Reaching the end of the page crosses no section's edge: the scroll itself tells.
    window.addEventListener('scroll', mark, { passive: true })
    return () => {
      window.removeEventListener('hashchange', follow)
      window.removeEventListener('scroll', mark)
      observer.disconnect()
      letGo(held)
    }
  }, [key])

  function show(event: MouseEvent<HTMLAnchorElement>, id: Id): void {
    const target = document.getElementById(id)
    if (target === null) {
      return
    }
    event.preventDefault()
    hold(held)
    const still = matchesMedia('(prefers-reduced-motion: reduce)', true)
    target.scrollIntoView({ behavior: still ? 'auto' : 'smooth', block: 'start' })
    target.focus({ preventScroll: true })
    // The router's own history entry is kept: only its hash changes, so the link can be shared.
    window.history.replaceState(window.history.state, '', `#${id}`)
    setCurrent(id)
  }

  return [current, show]
}
