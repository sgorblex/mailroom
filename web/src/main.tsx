import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nextProvider } from 'react-i18next'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'

import { AuthProvider } from '@/auth/auth-provider.tsx'
import { ClearOnSignOut } from '@/auth/clear-on-sign-out.ts'
import i18n, { i18nReady } from '@/i18n/i18n.ts'
import { createQueryClient } from '@/lib/query-client.ts'
import { router } from '@/router.tsx'
import './index.css'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Missing root element with id "root".')
}

const queryClient = createQueryClient()

void i18nReady.then(() => {
  createRoot(rootElement).render(
    <StrictMode>
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <ClearOnSignOut />
            <RouterProvider router={router} />
          </AuthProvider>
        </QueryClientProvider>
      </I18nextProvider>
    </StrictMode>,
  )
})
