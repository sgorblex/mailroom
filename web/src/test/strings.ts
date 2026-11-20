/** Every key path of a strings file, with each plural form as itself: the two languages' forms match. */
export function keys(strings: object, prefix = ''): string[] {
  return Object.entries(strings).flatMap(([key, value]: [string, unknown]) =>
    typeof value === 'object' && value !== null ? keys(value, `${prefix}${key}.`) : [`${prefix}${key}`],
  )
}
