import { measureElement, type Virtualizer } from '@tanstack/react-virtual'

/**
 * A message row's height for the list's virtualizer: what the browser measures, unless that is
 * nothing. A list hidden (an open message taking a phone's screen) lays no row out, and its rows
 * reported at no height would leave it none once shown again: no height, so no row in view to be
 * measured anew, and an empty list until a reload. A row hidden keeps the height it last had, or
 * the estimate.
 */
export function measureRow<TScroll extends Element, TItem extends Element>(
  element: TItem,
  entry: ResizeObserverEntry | undefined,
  instance: Virtualizer<TScroll, TItem>,
): number {
  const size = measureElement(element, entry, instance)
  if (size > 0) {
    return size
  }
  const index = instance.indexFromElement(element)
  return instance.itemSizeCache.get(instance.options.getItemKey(index)) ?? instance.options.estimateSize(index)
}
