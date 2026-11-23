/** `value` kept within `min` and `max` (`min` winning should `max` fall below it). */
export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, max))
}
