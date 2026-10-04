import './styles/index.css'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setNonce } from 'get-nonce'
import { lazy, StrictMode, Suspense, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { AuthProvider, useAuth } from '@/auth'
import { ErrorState, Loading } from '@/components/app'
import { TooltipProvider } from '@/components/ui/tooltip'
import { SignIn } from '@/pages/SignIn'

// Each screen is its own file, loaded when first opened.
const Ask = lazy(() => import('@/pages/Ask').then((m) => ({ default: m.Ask })))
const Shell = lazy(() => import('@/components/Shell').then((m) => ({ default: m.Shell })))
const Queue = lazy(() => import('@/pages/Queue').then((m) => ({ default: m.Queue })))
const NextAction = lazy(() => import('@/pages/NextAction').then((m) => ({ default: m.NextAction })))
const Approvals = lazy(() => import('@/pages/Approvals').then((m) => ({ default: m.Approvals })))
const DecisionCard = lazy(() => import('@/pages/DecisionCard').then((m) => ({ default: m.DecisionCard })))
const IdentityQueuePage = lazy(() => import('@/pages/Identity').then((m) => ({ default: m.IdentityQueuePage })))
const IdentityPairPage = lazy(() => import('@/pages/Identity').then((m) => ({ default: m.IdentityPairPage })))
const StatusPage = lazy(() => import('@/pages/Status').then((m) => ({ default: m.StatusPage })))
const ReportPage = lazy(() => import('@/pages/Status').then((m) => ({ default: m.ReportPage })))
const TeamAccounts = lazy(() => import('@/pages/TeamAccounts').then((m) => ({ default: m.TeamAccounts })))
const Tables = lazy(() => import('@/pages/Tables').then((m) => ({ default: m.Tables })))

// The server puts a fresh nonce in this meta tag on every page load and allows it in the Content Security Policy;
// the dialog scroll-lock uses it for its one <style> element. Nothing else inline is allowed.
const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content
if (nonce && nonce !== '__CSP_NONCE__') setNonce(nonce)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (n, e) => n < 1 && !(e instanceof ApiError && e.status >= 400 && e.status < 500),
      refetchOnWindowFocus: false,
      gcTime: 5 * 60 * 1000, // memory only; cleared on sign-out
    },
  },
})

function Home() {
  const { me } = useAuth()
  if (!me) return null
  if (me.can.my_queue && me.features.queue) return <Navigate to="/queue" replace />
  if (me.can.approvals && me.features.queue) return <Navigate to="/approvals" replace />
  if (me.can.manage_agents) return <Navigate to="/team" replace />
  return <Navigate to="/ask" replace />
}

function Guard({ allow, children }: { allow: boolean; children: ReactNode }) {
  return allow ? <>{children}</> : <ErrorState error={new ApiError(403, 'forbidden', 'Your role does not have access to this page.', null)} />
}

function App() {
  const { me, loading } = useAuth()
  if (loading)
    return (
      <div className="mx-auto max-w-md pt-40">
        <Loading rows={3} />
      </div>
    )
  if (!me) return <SignIn />
  return (
    <Suspense fallback={<div className="console-width py-10"><Loading /></div>}>
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="ask" element={<Ask />} />
        <Route path="queue" element={<Guard allow={me.can.my_queue}><Queue /></Guard>} />
        <Route path="next-action" element={<NextAction />} />
        <Route path="approvals" element={<Guard allow={me.can.approvals}><Approvals /></Guard>} />
        <Route path="team" element={<Guard allow={me.can.manage_agents}><TeamAccounts /></Guard>} />
        <Route path="cases/:recId" element={<DecisionCard />} />
        <Route path="identity" element={<Guard allow={me.can.identity_review}><IdentityQueuePage /></Guard>} />
        <Route path="identity/:pairId" element={<Guard allow={me.can.identity_review}><IdentityPairPage /></Guard>} />
        <Route path="status" element={<StatusPage />} />
        <Route path="tables" element={<Guard allow={me.demo && me.can.manage_agents}><Tables /></Guard>} />
        <Route path="tables/:schema/:name" element={<Guard allow={me.demo && me.can.manage_agents}><Tables /></Guard>} />
        <Route path="status/reports/:name" element={<ReportPage />} />
        <Route path="*" element={<ErrorState error={new ApiError(404, 'not_found', 'This page does not exist.', null)} />} />
      </Route>
    </Routes>
    </Suspense>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <TooltipProvider>
          <AuthProvider>
            <App />
          </AuthProvider>
        </TooltipProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
