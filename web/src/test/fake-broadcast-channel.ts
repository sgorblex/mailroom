import { vi } from 'vitest'

/**
 * A stand-in for the browser's BroadcastChannel: what one posts reaches at once every other open
 * channel of the same name, as another tab would hear it, cloned. The test plays another tab with
 * its own `new FakeBroadcastChannel(name)`, whose `received` keeps what it heard. Install it with
 * `stubBroadcastChannel`.
 */
export class FakeBroadcastChannel extends EventTarget {
  static open: FakeBroadcastChannel[] = []

  readonly name: string
  readonly received: unknown[] = []

  constructor(name: string) {
    super()
    this.name = name
    FakeBroadcastChannel.open.push(this)
  }

  postMessage(data: unknown): void {
    for (const channel of FakeBroadcastChannel.open) {
      if (channel !== this && channel.name === this.name) {
        const copy: unknown = structuredClone(data)
        channel.received.push(copy)
        channel.dispatchEvent(new MessageEvent('message', { data: copy }))
      }
    }
  }

  close(): void {
    FakeBroadcastChannel.open = FakeBroadcastChannel.open.filter((channel) => channel !== this)
  }
}

/** Every `BroadcastChannel` is a `FakeBroadcastChannel` until `vi.unstubAllGlobals()`. */
export function stubBroadcastChannel(): void {
  FakeBroadcastChannel.open = []
  vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
}
