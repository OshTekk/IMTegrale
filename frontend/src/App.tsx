import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Logo } from "./components/Logo";
import { PublicLayout } from "./components/PublicLayout";
import { SecuritySetupModal } from "./components/SecuritySetupModal";
import { LoginPage } from "./pages/LoginPage";
import { clearAccountState, queryKeys, replaceSessionState, useSession } from "./lib/queries";
import { broadcastSessionChange, subscribeToSessionChanges } from "./lib/sessionSync";

const OverviewPage = lazy(() => import("./pages/OverviewPage").then((module) => ({ default: module.OverviewPage })));
const NotesPage = lazy(() => import("./pages/NotesPage").then((module) => ({ default: module.NotesPage })));
const UesPage = lazy(() => import("./pages/UesPage").then((module) => ({ default: module.UesPage })));
const SharingPage = lazy(() => import("./pages/SharingPage").then((module) => ({ default: module.SharingPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const LeaderboardPage = lazy(() => import("./pages/LeaderboardPage").then((module) => ({ default: module.LeaderboardPage })));
const AdminPortal = lazy(() => import("./pages/AdminPortal").then((module) => ({ default: module.AdminPortal })));
const TrustPage = lazy(() => import("./pages/TrustPage").then((module) => ({ default: module.TrustPage })));
const DemoPage = lazy(() => import("./pages/DemoPage").then((module) => ({ default: module.DemoPage })));

export default function App() {
  const location = useLocation();
  if (location.pathname.startsWith("/admin")) {
    return <Suspense fallback={<div className="route-loading skeleton" />}><AdminPortal /></Suspense>;
  }
  if (location.pathname === "/confiance" || location.pathname === "/demo") {
    return (
      <Suspense fallback={<div className="route-loading skeleton" />}>
        <Routes>
          <Route element={<PublicLayout />}>
            <Route path="confiance" element={<TrustPage />} />
            <Route path="demo" element={<DemoPage />} />
          </Route>
        </Routes>
      </Suspense>
    );
  }
  return <StudentApp />;
}

function StudentApp() {
  const session = useSession();
  const queryClient = useQueryClient();

  useEffect(() => {
    const handleUnauthorized = () => {
      replaceSessionState(queryClient, { authenticated: false });
      broadcastSessionChange();
    };
    window.addEventListener("botnote:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("botnote:unauthorized", handleUnauthorized);
  }, [queryClient]);

  useEffect(() => subscribeToSessionChanges(() => {
    clearAccountState(queryClient);
    queryClient.invalidateQueries({ queryKey: queryKeys.session });
  }), [queryClient]);

  if (session.isPending) {
    return <div className="app-loading"><Logo /><span className="loading-line" /></div>;
  }
  if (!session.data?.authenticated || !session.data.account || !session.data.role) {
    return <LoginPage />;
  }

  const isOwner = session.data.role === "owner";
  return (
    <Suspense fallback={<div className="route-loading skeleton" />}>
      <Routes>
        <Route element={<AppShell session={session.data} />}>
          <Route index element={<OverviewPage />} />
          <Route path="notes" element={<NotesPage role={session.data.role} />} />
          <Route path="ues" element={<UesPage role={session.data.role} />} />
          <Route path="leaderboard" element={isOwner ? <LeaderboardPage /> : <Navigate to="/" replace />} />
          <Route path="sharing" element={isOwner ? <SharingPage /> : <Navigate to="/" replace />} />
          <Route path="settings" element={<SettingsPage role={session.data.role} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <SecuritySetupModal
        open={Boolean(isOwner && session.data.needs_security_setup)}
        onComplete={() => queryClient.invalidateQueries({ queryKey: queryKeys.session })}
      />
    </Suspense>
  );
}
