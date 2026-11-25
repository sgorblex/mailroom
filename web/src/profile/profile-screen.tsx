import { useState, type ReactElement, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useFeature, useMailLayer } from '@/auth/auth-context.ts'
import { useSectionSpy } from '@/lib/use-section-spy.ts'
import { cn } from '@/lib/utils.ts'
import { AppearanceSettings } from '@/profile/appearance-settings.tsx'
import { ConnectedApps } from '@/profile/connected-apps.tsx'
import { ConversationsForm } from '@/profile/conversations-form.tsx'
import { IndexSettings } from '@/profile/index-settings.tsx'
import { LimitsForm } from '@/profile/limits-form.tsx'
import { LinkedSignIn } from '@/profile/linked-sign-in.tsx'
import { MarkReadForm } from '@/profile/mark-read-form.tsx'
import { NotificationSettings } from '@/profile/notification-settings.tsx'
import { PasswordForm } from '@/profile/password-form.tsx'
import { RemoteSenders, TextSenders } from '@/profile/remembered-senders.tsx'
import { Sessions } from '@/profile/sessions.tsx'
import { SignatureSettings } from '@/profile/signature-settings.tsx'
import { TimeZoneForm } from '@/profile/time-zone-form.tsx'
import { UndoSendForm } from '@/profile/undo-send-form.tsx'
import { VacationSettings } from '@/profile/vacation-settings.tsx'

/** The Profile's groups of sections, in page order; each id is also its anchor (`/profile#mail`). */
type GroupId = 'account' | 'appearance' | 'mail' | 'sending' | 'apps'

interface Group {
  id: GroupId
  /** The group's sections, those this instance offers; a section that renders nothing leaves no card. */
  sections: { key: string; node: ReactNode }[]
}

/**
 * The signed-in user's own page, its sections in groups with a nav to them: Account (password,
 * time zone, linked sign-ins, signed-in sessions), Appearance, Mail with the mail client (what "Mark all as read"
 * does first on a folder with subfolders, conversations, the senders whose remote images they show
 * and those whose mail they read as text, the mail index and desktop notifications), Sending (how
 * long Send waits for an Undo and each account's vacation reply with the mail client, the
 * identities' signatures and the mail limits where the instance sends) and, with MCP, Apps
 * (connected apps). On a desktop the nav sits on the left and marks the group in view; on a phone
 * it scrolls sideways above the groups.
 */
export function ProfileScreen(): ReactElement {
  const { t } = useTranslation()
  const mcp = useFeature('mcp')
  const mail = useFeature('mail')
  const sends = useMailLayer()
  const [passwordChanges, setPasswordChanges] = useState(0)

  const groups: Group[] = [
    {
      id: 'account',
      sections: [
        {
          key: 'password',
          node: (
            <PasswordForm
              onChanged={() => {
                setPasswordChanges((count) => count + 1)
              }}
            />
          ),
        },
        { key: 'time-zone', node: <TimeZoneForm /> },
        { key: 'linked-sign-in', node: <LinkedSignIn /> },
        { key: 'sessions', node: <Sessions reloadKey={passwordChanges} /> },
      ],
    },
    { id: 'appearance', sections: [{ key: 'appearance', node: <AppearanceSettings /> }] },
    ...(mail
      ? [
          {
            id: 'mail' as const,
            sections: [
              { key: 'mark-read', node: <MarkReadForm /> },
              { key: 'conversations', node: <ConversationsForm /> },
              { key: 'remote-senders', node: <RemoteSenders /> },
              { key: 'text-senders', node: <TextSenders /> },
              { key: 'index', node: <IndexSettings /> },
              { key: 'notifications', node: <NotificationSettings /> },
            ],
          },
        ]
      : []),
    ...(mail || sends
      ? [
          {
            id: 'sending' as const,
            sections: [
              ...(mail ? [{ key: 'undo-send', node: <UndoSendForm /> }] : []),
              ...(sends ? [{ key: 'signatures', node: <SignatureSettings /> }] : []),
              ...(mail ? [{ key: 'vacation', node: <VacationSettings /> }] : []),
              ...(sends ? [{ key: 'limits', node: <LimitsForm /> }] : []),
            ],
          },
        ]
      : []),
    ...(mcp ? [{ id: 'apps' as const, sections: [{ key: 'connected-apps', node: <ConnectedApps /> }] }] : []),
  ]
  const ids = groups.map((group) => group.id)
  const [current, show] = useSectionSpy(ids)

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 p-4 md:p-6 lg:gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('profile.title')}</h1>
      <div className="flex flex-col gap-6 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:items-start lg:gap-10">
        <nav
          aria-label={t('profile.sections.label')}
          className="sticky top-(--deploy-banner) z-10 -mx-4 border-b bg-background px-4 py-2 md:-mx-6 md:px-6 lg:top-[calc(var(--deploy-banner)+1.5rem)] lg:mx-0 lg:border-b-0 lg:p-0"
        >
          <ul className="flex gap-1 overflow-x-auto text-sm lg:flex-col lg:overflow-visible">
            {ids.map((id) => (
              <li key={id} className="shrink-0">
                <a
                  href={`#${id}`}
                  aria-current={id === current ? 'location' : undefined}
                  className={cn(
                    'block rounded-md px-3 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    id === current
                      ? 'bg-accent-soft font-medium text-accent-soft-foreground'
                      : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                  )}
                  onClick={(event) => {
                    show(event, id)
                  }}
                >
                  {t(`profile.sections.${id}`)}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex min-w-0 flex-col gap-10">
          {groups.map((group) => (
            <section
              key={group.id}
              id={group.id}
              tabIndex={-1}
              aria-labelledby={`${group.id}-group`}
              className="flex scroll-mt-16 flex-col gap-4 outline-none lg:scroll-mt-6"
            >
              <h2 id={`${group.id}-group`} className="text-lg font-semibold tracking-tight">
                {t(`profile.sections.${group.id}`)}
              </h2>
              <div className="grid items-start gap-4 xl:grid-cols-2">
                {group.sections.map((section) => (
                  <div key={section.key} className="profile-card min-w-0 rounded-xl border bg-card p-4 empty:hidden md:p-5">
                    {section.node}
                  </div>
                ))}
                <p className="text-sm text-muted-foreground [.profile-card:not(:empty)~&]:hidden">
                  {t('profile.sections.empty')}
                </p>
              </div>
            </section>
          ))}
        </div>
      </div>
    </main>
  )
}
