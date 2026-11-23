import { useQuery, useQueryClient } from '@tanstack/react-query'
import { lazy, Suspense, useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { accountSignatures, listAccounts, saveSignature, type Account, type IdentitySignature } from '@/api/accounts.ts'
import { apiErrorMessage } from '@/api/http.ts'
import { Button } from '@/components/ui/button.tsx'
import { ACCOUNT_NOT_FOUND, mailErrorMessage } from '@/mail/messages.ts'
import { ACCOUNTS_KEY, READ_ON_MOUNT, signaturesKey } from '@/mail/queries.ts'

// TipTap is most of the editor's weight: the profile loads without it.
const RichEditor = lazy(() => import('@/mail/rich-editor.tsx').then((module) => ({ default: module.RichEditor })))

/**
 * A signature per identity of each account, written in the composer's rich editor (no images).
 * Messages from the identity start with it, below where the text goes and above a quote.
 */
export function SignatureSettings(): ReactElement {
  const { t } = useTranslation()
  const accounts = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: ({ signal }) => listAccounts(signal), ...READ_ON_MOUNT })
  return (
    <section aria-labelledby="signatures" className="flex flex-col gap-3">
      <h3 id="signatures" className="font-medium">
        {t('signatures.title')}
      </h3>
      <p className="text-sm text-muted-foreground">{t('signatures.intro')}</p>
      {accounts.isError && (
        <p role="alert" className="text-sm text-destructive">
          {apiErrorMessage(accounts.error, t('signatures.loadFailed'))}
        </p>
      )}
      {accounts.data?.map((account) => <AccountSignatures key={account.id} account={account} />)}
    </section>
  )
}

function AccountSignatures({ account }: { account: Account }): ReactElement {
  const { t } = useTranslation()
  const signatures = useQuery({
    queryKey: signaturesKey(account.id),
    queryFn: ({ signal }) => accountSignatures(account.id, signal),
    ...READ_ON_MOUNT,
  })
  const headingId = `signatures-${String(account.id)}`
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h4 id={headingId} className="text-sm font-medium">
        {account.display_name}
      </h4>
      {signatures.isPending && <p className="text-sm text-muted-foreground">{t('signatures.loading')}</p>}
      {signatures.isError && (
        <p role="alert" className="text-sm text-destructive">
          {mailErrorMessage(t, signatures.error, t('signatures.loadFailed'), ACCOUNT_NOT_FOUND)}
        </p>
      )}
      {signatures.data?.map((signature) => <SignatureEditor key={signature.address} account={account} signature={signature} />)}
    </section>
  )
}

function SignatureEditor({ account, signature }: { account: Account; signature: IdentitySignature }): ReactElement {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const labelId = useId()
  const [html, setHtml] = useState(signature.html)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<{ kind: 'saved' } | { kind: 'failed'; message: string } | null>(null)

  async function save(): Promise<void> {
    setSaving(true)
    setOutcome(null)
    try {
      const saved = await saveSignature(account.id, signature.address, html)
      queryClient.setQueryData<IdentitySignature[]>(signaturesKey(account.id), (all) =>
        all?.map((each) => (each.address === saved.address ? saved : each)),
      )
      setOutcome({ kind: 'saved' })
    } catch (error) {
      setOutcome({ kind: 'failed', message: mailErrorMessage(t, error, t('signatures.saveFailed')) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-sm text-muted-foreground">
        {t('signatures.of', { address: signature.address })}
      </span>
      <Suspense fallback={<p className="text-sm text-muted-foreground">{t('signatures.loading')}</p>}>
        <RichEditor
          focusStart={false}
          labelId={labelId}
          html={signature.html}
          inline={[]}
          locked={saving}
          onChange={(changed) => {
            setHtml(changed)
            setOutcome(null)
          }}
          onImages={() => null}
          onFiles={() => undefined}
          placeholder={t('signatures.placeholder')}
        />
      </Suspense>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" disabled={saving} onClick={() => void save()}>
          {t('signatures.save')}
        </Button>
        {outcome?.kind === 'saved' && (
          <span role="status" className="text-sm">
            {t('signatures.saved')}
          </span>
        )}
        {outcome?.kind === 'failed' && (
          <span role="alert" className="text-sm text-destructive">
            {outcome.message}
          </span>
        )}
      </div>
    </div>
  )
}
