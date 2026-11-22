import { useCallback, useMemo, useSyncExternalStore } from 'react'

/**
 * Choices of this device alone (a pane's width, a collapsed folder pane), kept in its browser as
 * JSON under `mailroom.ui.<name>`; the user's look on every device is on the server instead
 * (`useAppearance`). Storage may refuse (a private window): a choice then lasts until reload, and
 * one that does not read back as valid is the fallback.
 */

const PREFIX = 'mailroom.ui.'

/** Whether a stored value is one the caller can use. */
export type Validate<T> = (value: unknown) => value is T

/** Choices written while storage refused them, by name: they last until reload. */
const unstored = new Map<string, string>()
const listeners = new Set<() => void>()

function rawPref(name: string): string | null {
  const held = unstored.get(name)
  if (held !== undefined) {
    return held
  }
  try {
    return window.localStorage.getItem(PREFIX + name)
  } catch {
    return null
  }
}

function parsed<T>(raw: string | null, fallback: T, validate: Validate<T>): T {
  if (raw === null) {
    return fallback
  }
  try {
    const value: unknown = JSON.parse(raw)
    return validate(value) ? value : fallback
  } catch {
    return fallback
  }
}

/** This device's choice `name`, or `fallback` when none is kept or it is not valid. */
export function readDevicePref<T>(name: string, fallback: T, validate: Validate<T>): T {
  return parsed(rawPref(name), fallback, validate)
}

/** Keeps `value` as this device's choice `name`; every `useDevicePref` of it, in any tab, follows. */
export function writeDevicePref(name: string, value: unknown): void {
  const raw = JSON.stringify(value)
  try {
    window.localStorage.setItem(PREFIX + name, raw)
    unstored.delete(name)
  } catch {
    unstored.set(name, raw)
  }
  for (const listener of listeners) {
    listener()
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  // Fired by another tab's write; this tab's own writes call the listeners directly.
  window.addEventListener('storage', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', listener)
  }
}

/**
 * This device's choice `name` (else `fallback`) and a setter, kept in step across components and
 * tabs. Pass a `fallback` and `validate` that keep their identity (module constants), as the
 * value is read again when they change.
 */
export function useDevicePref<T>(name: string, fallback: T, validate: Validate<T>): [T, (value: T) => void] {
  const raw = useSyncExternalStore(subscribe, () => rawPref(name))
  const value = useMemo(() => parsed(raw, fallback, validate), [raw, fallback, validate])
  const set = useCallback((next: T) => {
    writeDevicePref(name, next)
  }, [name])
  return [value, set]
}
