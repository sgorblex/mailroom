import { vi } from 'vitest'

/**
 * A stand-in for the browser's Notification: each one shown is in `instances`, oldest first, and
 * the test clicks it. `permission` is what the browser answers; `requestPermission` answers
 * `answer` and keeps it. Install it with `stubNotification`.
 */
export class FakeNotification extends EventTarget {
  static permission: NotificationPermission = 'default'
  static answer: NotificationPermission = 'granted'
  static instances: FakeNotification[] = []
  static requestPermission = vi.fn((): Promise<NotificationPermission> => {
    FakeNotification.permission = FakeNotification.answer
    return Promise.resolve(FakeNotification.permission)
  })

  readonly title: string
  readonly body: string | undefined
  readonly tag: string | undefined
  closed = false

  constructor(title: string, options?: NotificationOptions) {
    super()
    this.title = title
    this.body = options?.body
    this.tag = options?.tag
    FakeNotification.instances.push(this)
  }

  close(): void {
    this.closed = true
  }

  click(): void {
    this.dispatchEvent(new Event('click', { cancelable: true }))
  }
}

/** Every `Notification` is a `FakeNotification` with `permission` until `vi.unstubAllGlobals()`. */
export function stubNotification(
  permission: NotificationPermission,
  answer: NotificationPermission = 'granted',
): typeof FakeNotification {
  FakeNotification.permission = permission
  FakeNotification.answer = answer
  FakeNotification.instances = []
  FakeNotification.requestPermission.mockClear()
  vi.stubGlobal('Notification', FakeNotification)
  return FakeNotification
}

/**
 * A browser that has the API but throws from its constructor, as Chrome for Android does: a new
 * stand-in each time, so that what the app learnt of the previous one does not carry over.
 */
export function stubRefusingNotification(permission: NotificationPermission): void {
  stubNotification(permission)
  const refusing = new Proxy(FakeNotification, {
    construct() {
      throw new TypeError('Illegal constructor. Use ServiceWorkerRegistration.showNotification() instead.')
    },
  })
  vi.stubGlobal('Notification', refusing)
}
