import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import './styles/index.css'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ApiError } from './api/client'
import { AuthProvider, useAuth } from './auth'
import { Shell } from './components/Shell'
import { ErrorState, Spinner } from './components/ui'
import { Approvals } from './pages/Approvals'
import { Ask } from './pages/Ask'
import { DecisionCard } from './pages/DecisionCard'
import { IdentityPairPage, IdentityQueuePage } from './pages/Identity'
import { Queue } from './pages/Queue'
import { SignIn } from './pages/SignIn'
import { ReportPage, StatusPage } from './pages/Status'

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
  if (me.can.identity_review) return <Navigate to="/identity" replace />
  return <Navigate to="/ask" replace />
}

function Guard({ allow, children }: { allow: boolean; children: React.ReactNode }) {
  return allow ? <>{children}</> : <ErrorState error={new ApiError(403, 'forbidden', 'Your role does not have access to this page.', null)} />
}

function App() {
  const { me, loading } = useAuth()
  if (loading)
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    )
  if (!me) return <SignIn />
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="ask" element={<Ask />} />
        <Route path="queue" element={<Guard allow={me.can.my_queue}><Queue /></Guard>} />
        <Route path="approvals" element={<Guard allow={me.can.approvals || me.role === 'qa_analyst'}><Approvals /></Guard>} />
        <Route path="cases/:recId" element={<DecisionCard />} />
        <Route path="identity" element={<Guard allow={me.can.identity_review}><IdentityQueuePage /></Guard>} />
        <Route path="identity/:pairId" element={<Guard allow={me.can.identity_review}><IdentityPairPage /></Guard>} />
        <Route path="status" element={<StatusPage />} />
        <Route path="status/reports/:name" element={<ReportPage />} />
        <Route path="*" element={<ErrorState error={new ApiError(404, 'not_found', 'This page does not exist.', null)} />} />
      </Route>
    </Routes>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
