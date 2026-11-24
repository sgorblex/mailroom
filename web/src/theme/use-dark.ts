import { useSyncExternalStore } from 'react'

/** Whether the page shows dark now: the `.dark` class `look.ts` sets on <html>. */
function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}

function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => {
    observer.disconnect()
  }
}

/** The page's resolved theme is dark, following each change of it (a choice or the OS's). */
export function useDark(): boolean {
  return useSyncExternalStore(subscribe, isDark)
}
