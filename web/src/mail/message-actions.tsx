import { createElement, useEffect, useId, useLayoutEffect, useRef, useState, type ReactElement, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { MoreHorizontal } from 'lucide-react'

import { useAccountName } from '@/accounts/accounts-context.ts'
import type { AccountId } from '@/api/accounts.ts'
import { Button } from '@/components/ui/button.tsx'
import { Menu, type MenuItem } from '@/components/ui/menu.tsx'
import { LG_QUERY, useMediaQuery } from '@/lib/media-query.ts'
import { cn } from '@/lib/utils.ts'

import { overflowingActions, type BarItem } from './action-overflow.ts'
import { listedActions, triageShortcut, unavailableReason, type ActionContext, type MailAction } from './action-registry.ts'
import { useCompose } from './compose-context.ts'
import { useDraftFilter } from './filter-draft.ts'
import { MAIL_NS } from './i18n.ts'
import { useMuting } from './mute-context.ts'
import { useUGoesBack } from './reading-pane.ts'
import { overlayOpen } from './shortcuts.ts'
import { useSnoozing } from './snooze-context.ts'
import { TriageButtons, TriagePickers } from './toolbar.tsx'
import { useTriageActions } from './triage-actions.ts'
import { useOpenMessage, useTriage } from './triage-context.ts'

type Picker = 'move' | 'account'

const NONE: ReadonlySet<string> = new Set()

function sameSet(one: ReadonlySet<string>, other: ReadonlySet<string>): boolean {
  return one.size === other.size && [...one].every((id) => other.has(id))
}

/**
 * The actions of the bar `row` that go to its More menu for the rest to fit on one row (see
 * `overflowingActions`), measured again as the row changes width and as `listed` (its actions, by
 * id) changes. A hidden button counts at the width it had when last shown. None until laid out.
 * Docked (at the foot of a narrow screen) or not, its buttons take other widths: on changing, all
 * show again to be measured anew.
 */
function useOverflow(row: RefObject<HTMLElement | null>, listed: string, docked: boolean): ReadonlySet<string> {
  const [overflow, setOverflow] = useState<ReadonlySet<string>>(NONE)
  const [measuredDocked, setMeasuredDocked] = useState(docked)
  if (measuredDocked !== docked) {
    setMeasuredDocked(docked)
    setOverflow(NONE)
  }
  const widths = useRef(new Map<string, number>())
  useLayoutEffect(() => {
    const element = row.current
    if (element === null || typeof ResizeObserver === 'undefined') {
      return
    }
    const fit = (): void => {
      const items: BarItem[] = [...element.children].flatMap((child, index) => {
        if (!(child instanceof HTMLElement)) {
          return []
        }
        const id = child.dataset['action'] ?? `fixed ${String(index)}`
        if (!child.hidden) {
          // The separator's room is mostly its margins.
          const style = getComputedStyle(child)
          const margins = (Number.parseFloat(style.marginLeft) || 0) + (Number.parseFloat(style.marginRight) || 0)
          widths.current.set(id, child.offsetWidth + margins)
        }
        return [{ id, width: widths.current.get(id) ?? 0 }]
      })
      const gap = Number.parseFloat(getComputedStyle(element).columnGap)
      const next = element.clientWidth > 0 ? overflowingActions(items, element.clientWidth, Number.isNaN(gap) ? 0 : gap) : NONE
      setOverflow((current) => (sameSet(current, next) ? current : next))
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(element)
    return () => {
      observer.disconnect()
    }
  }, [row, listed, docked])
  return overflow
}

/**
 * The open message's action bar, all from the action registry: Reply, Reply all and Forward, which
 * open the composer; its triage actions as icon buttons (on the message, or the conversation it
 * stands for, whatever is selected in the list); the rest (print, download, a filter drafted from
 * it, mute, snooze) in a More menu. All on one row: the buttons that do not fit go to the top
 * of the More menu, in the order `OVERFLOW_ORDER` gives them up. Once muted, says
 * so, and when the account's Run filters is off, that muting has no effect until it is on.
 *
 * With nothing selected the screen's keys act on the open message too, so its pickers are the
 * screen's (`v` opens the move picker here); with a selection those are the list toolbar's, and
 * this bar keeps its own.
 *
 * On a narrow screen the bar is fixed at its foot, above the phone's tab bar and the home
 * indicator, Reply, Reply all and Forward as icons. Its height is then
 * `--action-bar`, which the toasts and the reading pane leave free.
 */
export function MessageActions({ account, messageRef }: { account: AccountId; messageRef: string }): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const triage = useTriage()
  const uGoesBack = useUGoesBack()
  const compose = useCompose()
  const draftFilter = useDraftFilter()
  const muting = useMuting()
  const snoozing = useSnoozing()
  const accountName = useAccountName()
  const hintId = useId()
  const docked = !useMediaQuery(LG_QUERY, true)
  const bar = useRef<HTMLDivElement>(null)
  useDockedHeight(bar, docked)
  // The triage actions wait for the view to have set the open message (its flags, its row).
  const opened = useOpenMessage()
  const open = opened?.ref === messageRef ? opened : null
  const actions = useTriageActions(open === null ? [] : [open])
  const shared = triage.selection.size === 0
  const [own, setOwn] = useState<Picker | null>(null)
  const picker = shared ? triage.picker : own === 'move'
  const accountPicker = shared ? triage.accountPicker : own === 'account'
  const trigger = useRef<HTMLButtonElement>(null)
  const accountTrigger = useRef<HTMLButtonElement>(null)
  const row = useRef<HTMLDivElement>(null)
  const moreButton = useRef<HTMLButtonElement>(null)
  // The More button the menu is open under.
  const [menu, setMenu] = useState<HTMLElement | null>(null)
  if (shared && own !== null) {
    setOwn(null)
  }
  // Escape closes this bar's own picker, before the screen's keys see it (they would clear the
  // selection); a dialog or a menu open over it takes Escape first.
  useEffect(() => {
    if (own === null) {
      return
    }
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape' && !event.defaultPrevented && !overlayOpen()) {
        event.preventDefault()
        setOwn(null)
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [own])

  const closePicker = (kind: Picker): void => {
    if (!shared) {
      setOwn(null)
    } else if (kind === 'move') {
      triage.setPicker(false)
    } else {
      triage.setAccountPicker(false)
    }
  }
  const context: ActionContext = {
    actions,
    message: { ref: messageRef, account_id: account },
    account,
    compose,
    openPicker: (kind) => {
      if (!shared) {
        setOwn((current) => (current === kind ? null : kind))
      } else if (kind === 'move') {
        triage.setPicker(!picker)
      } else {
        triage.setAccountPicker(!accountPicker)
      }
    },
    draftFilter,
    muting,
    snoozing,
  }
  const muted = muting?.mutedAs(messageRef)
  const answers = listedActions('reply', context)
  const allowed = answers.every((action) => unavailableReason(action, context) === null)
  const forms = listedActions('message', context)
  const triageListed = listedActions('triage', context)
  // Labels in another language take another width.
  const overflow = useOverflow(
    row,
    [...answers, ...triageListed].map((action) => action.id).join(' ') + ` ${i18n.language}`,
    docked,
  )
  const overflowed = [...answers, ...triageListed].filter((action) => overflow.has(action.id))
  const answerItem = (action: MailAction): MenuItem => ({
    id: action.id,
    label: t(action.label(context)),
    icon: createElement(action.icon(context), { 'aria-hidden': true, className: 'size-4' }),
    shortcut: action.shortcut,
    disabledReason: allowed ? null : t('compose.offHint'),
    onSelect: () => {
      action.run(context)
    },
  })
  const triageItem = (action: MailAction): MenuItem => {
    const reason = unavailableReason(action, context)
    return {
      id: action.id,
      label: t(action.label(context)),
      icon: createElement(action.icon(context), { 'aria-hidden': true, className: 'size-4' }),
      shortcut: reason === null ? triageShortcut(action, shared, uGoesBack) : undefined,
      disabledReason: reason === null ? null : t(reason),
      onSelect: () => {
        if (!triage.busy) {
          action.run(context)
        }
      },
    }
  }
  const formItem = (action: MailAction): MenuItem => {
    const reason = unavailableReason(action, context)
    return {
      id: action.id,
      label: t(action.label(context)),
      icon: createElement(action.icon(context), { 'aria-hidden': true, className: 'size-4' }),
      disabledReason: reason === null ? null : t(reason),
      onSelect: () => {
        action.run(context)
      },
    }
  }

  return (
    <div
      ref={bar}
      className={cn(
        'flex flex-col gap-2',
        docked &&
          'fixed inset-x-0 bottom-(--tab-bar) z-30 max-h-[60dvh] overflow-y-auto border-t bg-background px-3 pt-2 pb-2 md:pb-[calc(0.5rem+env(safe-area-inset-bottom))]',
      )}
    >
      <div
        ref={row}
        role="group"
        aria-label={t('triage.label')}
        aria-busy={triage.busy}
        className="flex items-center gap-1"
      >
        {answers.map((action) => (
          <Button
            key={action.id}
            data-action={action.id}
            hidden={overflow.has(action.id)}
            type="button"
            variant="outline"
            size="sm"
            title={`${t(action.label(context))} (${action.shortcut ?? ''})`}
            aria-keyshortcuts={action.shortcut}
            aria-disabled={!allowed}
            aria-describedby={allowed ? undefined : hintId}
            // Keeps the text selected in the message, which a reply quotes.
            onMouseDown={(event) => {
              event.preventDefault()
            }}
            onClick={() => {
              if (allowed) {
                action.run(context)
              }
            }}
          >
            {createElement(action.icon(context), { 'aria-hidden': true })}
            <span className={cn(docked && 'sr-only sm:not-sr-only')}>{t(action.label(context))}</span>
          </Button>
        ))}
        <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />
        <TriageButtons
          context={context}
          busy={triage.busy}
          // With a selection the keys act on it, not on the open message these buttons act on.
          shortcuts={shared}
          picker={picker}
          accountPicker={accountPicker}
          trigger={trigger}
          accountTrigger={accountTrigger}
          overflow={overflow}
        />
        {(forms.length > 0 || overflowed.length > 0) && (
          <Button
            ref={moreButton}
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t('message.more')}
            title={t('message.more')}
            aria-haspopup="menu"
            aria-expanded={menu !== null}
            // The menu's click-outside does not see this press: a second click closes the menu.
            onPointerDown={(event) => {
              event.stopPropagation()
            }}
            onClick={(event) => {
              setMenu(menu === null ? event.currentTarget : null)
            }}
          >
            <MoreHorizontal aria-hidden="true" />
          </Button>
        )}
      </div>
      {menu !== null && (
        <Menu
          label={t('message.more')}
          anchor={menu}
          items={[
            ...overflowed.map((action) => (action.group === 'reply' ? answerItem(action) : triageItem(action))),
            ...forms.map(formItem),
          ]}
          onClose={() => {
            setMenu(null)
          }}
        />
      )}
      <TriagePickers
        actions={actions}
        picker={picker}
        accountPicker={accountPicker}
        into={shared ? triage.accountPickerInto : null}
        onClosePicker={() => {
          closePicker('move')
        }}
        onCloseAccountPicker={() => {
          closePicker('account')
        }}
        // A picker opened from the More menu gives focus back to the More button.
        trigger={overflow.has('move') ? moreButton : trigger}
        accountTrigger={overflow.has('moveToAccount') ? moreButton : accountTrigger}
      />
      {!allowed && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {t('compose.offHint')}
        </p>
      )}
      {muted !== undefined && (
        <p className="text-xs text-muted-foreground">
          {t('mute.isMuted')}
          {!muted.run_filters && ` ${t('mute.runFiltersOff', { account: accountName(account) })}`}
        </p>
      )}
    </div>
  )
}

/** Sets `--action-bar` to the height of the bar while it is `docked`: what the toasts and the page leave free. */
function useDockedHeight(bar: RefObject<HTMLDivElement | null>, docked: boolean): void {
  useLayoutEffect(() => {
    const element = bar.current
    if (!docked || element === null) {
      return
    }
    const root = document.documentElement.style
    const measure = (): void => {
      root.setProperty('--action-bar', `${String(element.offsetHeight)}px`)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    return () => {
      observer?.disconnect()
      root.removeProperty('--action-bar')
    }
  }, [bar, docked])
}
