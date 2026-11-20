import { vi } from 'vitest'

/**
 * A stand-in for the browser's EventSource: each one made is in `instances`, newest last, and
 * the test opens it, sends it events and fails it. Install it with `stubEventSource`.
 */
export class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  readonly url: string
  readonly withCredentials: boolean
  readyState: number = FakeEventSource.CONNECTING

  constructor(url: string, init?: EventSourceInit) {
    super()
    this.url = url
    this.withCredentials = init?.withCredentials ?? false
    FakeEventSource.instances.push(this)
  }

  /** The newest one not closed. */
  static current(): FakeEventSource | undefined {
    return FakeEventSource.instances.filter((each) => each.readyState !== FakeEventSource.CLOSED).at(-1)
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED
  }

  open(): void {
    this.readyState = FakeEventSource.OPEN
    this.dispatchEvent(new Event('open'))
  }

  /** An event of `type` with `data` as its JSON. */
  send(type: string, data: unknown): void {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }))
  }

  /** The connection failed, or the server refused it. */
  fail(): void {
    this.dispatchEvent(new Event('error'))
  }
}

/** Every `new EventSource` is a `FakeEventSource` until `vi.unstubAllGlobals()`. */
export function stubEventSource(): typeof FakeEventSource {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  return FakeEventSource
}
