import { useContext, useEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { AccountsContext, useAccountName } from '@/accounts/accounts-context.ts'
import { addFilter, addRuleSet, changeRuleSet, listRuleSets, setRunFilters, type RuleSet } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'

import { useFiltersOffered } from './filter-draft.ts'
import { MAIL_NS } from './i18n.ts'
import { useTriage, type SpamOffer } from './triage-context.ts'

/** Of a filter's name, as the API takes it. */
const MAX_NAME = 200

/**
 * The rule set of the account's inbox a spam filter goes to: one acting on new mail, an enabled
 * one first, else a scheduled one; `undefined` when there is none (a manual one never runs by itself).
 */
function inboxRuleSet(ruleSets: RuleSet[]): RuleSet | undefined {
  const inbox = ruleSets.filter(
    (ruleSet) => ruleSet.folder.length === 1 && ruleSet.folder[0]?.toUpperCase() === 'INBOX' && ruleSet.trigger !== 'manual',
  )
  const rank = (ruleSet: RuleSet): number => (ruleSet.trigger === 'new_mail' ? 0 : 2) + (ruleSet.enabled ? 0 : 1)
  return [...inbox].sort((a, b) => rank(a) - rank(b))[0]
}

/**
 * After a report of spam from one sender, where the instance runs filters: offers a filter moving
 * their mail to Junk, never adding it unasked. Once added, says whether its rule set and the
 * account's Run filters are on, each with a button to turn it on: neither is turned on unasked.
 */
export function SpamFilterOffer(): ReactElement | null {
  const { spamOffer, dismissSpamOffer } = useTriage()
  if (!useFiltersOffered() || spamOffer === null) {
    return null
  }
  return (
    <Offer key={`${String(spamOffer.account)} ${spamOffer.address}`} offer={spamOffer} onClose={dismissSpamOffer} />
  )
}

function Offer({ offer, onClose }: { offer: SpamOffer; onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accounts = useContext(AccountsContext)
  const accountName = useAccountName()
  const [ruleSet, setRuleSet] = useState<RuleSet | null>(null)
  const [runFilters, setRunFiltersState] = useState(
    accounts?.find((account) => account.id === offer.account)?.run_filters === true,
  )
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A rule set made here before adding the filter failed: the next try adds to it.
  const made = useRef<RuleSet | null>(null)
  const added = useRef<HTMLParagraphElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const { address } = offer

  // The button pressed is gone: focus goes to what replaced it.
  const addedTo = ruleSet?.id
  useEffect(() => {
    if (addedTo !== undefined) {
      added.current?.focus()
    }
  }, [addedTo])

  async function act(work: () => Promise<void>, failure: string): Promise<void> {
    if (pending) {
      return
    }
    setPending(true)
    setError(null)
    try {
      await work()
    } catch {
      setError(failure)
    } finally {
      setPending(false)
    }
  }

  async function addSpamFilter(): Promise<void> {
    let target = made.current ?? inboxRuleSet(await listRuleSets(offer.account))
    if (target === undefined) {
      target = await addRuleSet(offer.account, {
        name: t('spamOffer.newRuleSet'),
        folder: ['INBOX'],
        trigger: 'new_mail',
        interval_minutes: null,
        marker: 'none',
        enabled: false,
      })
      made.current = target
    }
    const name = t('spamOffer.filterName', { address })
    await addFilter(target.id, {
      // The sender's address comes from the mail, of any length.
      name: name.length > MAX_NAME ? `${name.slice(0, MAX_NAME - 1)}…` : name,
      enabled: true,
      spec: { criterion: { type: 'from_is', args: { addr: address } }, action: { type: 'junk' } },
    })
    setRuleSet(target)
  }

  return (
    <section aria-label={t('spamOffer.label')} className="flex flex-col gap-2 rounded-lg border p-3 text-sm">
      {ruleSet === null ? (
        <>
          <p className="font-medium break-words">{t('spamOffer.question', { address })}</p>
          <p className="text-muted-foreground">{t('spamOffer.explain')}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={pending} onClick={() => void act(addSpamFilter, t('spamOffer.addFailed'))}>
              {t('spamOffer.add')}
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t('spamOffer.noThanks')}
            </Button>
          </div>
        </>
      ) : (
        <>
          <p ref={added} tabIndex={-1} className="font-medium break-words outline-none">
            {t('spamOffer.added', { address, ruleSet: ruleSet.name })}
          </p>
          {!ruleSet.enabled && (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-muted-foreground">{t('spamOffer.ruleSetOff', { ruleSet: ruleSet.name })}</p>
              <Button
                size="sm"
                variant="outline"
                disabled={pending}
                onClick={() =>
                  void act(async () => {
                    setRuleSet(await changeRuleSet(ruleSet.id, { enabled: true }))
                    close.current?.focus()
                  }, t('spamOffer.turnOnFailed'))
                }
              >
                {t('spamOffer.turnOnRuleSet')}
              </Button>
            </div>
          )}
          {!runFilters && (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-muted-foreground">{t('spamOffer.runFiltersOff', { account: accountName(offer.account) })}</p>
              <Button
                size="sm"
                variant="outline"
                disabled={pending}
                onClick={() =>
                  void act(async () => {
                    setRunFiltersState(await setRunFilters(offer.account, true))
                    close.current?.focus()
                  }, t('spamOffer.turnOnFailed'))
                }
              >
                {t('spamOffer.turnOnRunFilters')}
              </Button>
            </div>
          )}
          {ruleSet.enabled && runFilters && <p className="text-muted-foreground">{t('spamOffer.active', { address })}</p>}
          <div>
            <Button ref={close} size="sm" variant="ghost" onClick={onClose}>
              {t('spamOffer.close')}
            </Button>
          </div>
        </>
      )}
      {error !== null && <p role="alert">{error}</p>}
    </section>
  )
}
