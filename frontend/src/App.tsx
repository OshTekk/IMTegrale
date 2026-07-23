import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useLayoutEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { Logo } from "./components/Logo";
import { PublicLayout } from "./components/PublicLayout";
import { SecuritySetupModal } from "./components/SecuritySetupModal";
import { SyncSetupModal } from "./components/SyncSetupModal";
import { SimulationLayout } from "./components/SimulationLayout";
import { LoginPage } from "./pages/LoginPage";
import { isPrimaryOwnerSession } from "./lib/auth";
import { learningDocumentTitle } from "./lib/learning";
import { clearAccountState, queryKeys, replaceSessionState, useSession } from "./lib/queries";
import { broadcastSessionChange, subscribeToSessionChanges } from "./lib/sessionSync";

const loadOverviewPage = () => import("./pages/OverviewPage");
const loadResultsPages = () => import("./pages/results");
const loadAcademicReportPage = () => import("./pages/AcademicReportPage");
const loadSharingPage = () => import("./pages/SharingPage");
const loadSettingsPage = () => import("./pages/SettingsPage");
const loadLeaderboardPage = () => import("./pages/LeaderboardPage");
const loadSimulationsPage = () => import("./pages/SimulationsPage");
const loadNoteSimulationsPage = () => import("./pages/NoteSimulationsPage");
const loadCalendarPage = () => import("./pages/CalendarPage");
const loadLearningPage = () => import("./pages/LearningPage");
const OverviewPage = lazy(() => loadOverviewPage().then((module) => ({ default: module.OverviewPage })));
const ResultsPage = lazy(() => loadResultsPages().then((module) => ({ default: module.ResultsPage })));
const ResultsUeDetailPage = lazy(() => loadResultsPages().then((module) => ({ default: module.ResultsUeDetailPage })));
const AcademicReportPage = lazy(() =>
  loadAcademicReportPage().then((module) => ({ default: module.AcademicReportPage })),
);
const SharingPage = lazy(() => loadSharingPage().then((module) => ({ default: module.SharingPage })));
const SettingsPage = lazy(() => loadSettingsPage().then((module) => ({ default: module.SettingsPage })));
const LeaderboardPage = lazy(() => loadLeaderboardPage().then((module) => ({ default: module.LeaderboardPage })));
const SimulationsPage = lazy(() => loadSimulationsPage().then((module) => ({ default: module.SimulationsPage })));
const NoteSimulationsPage = lazy(() =>
  loadNoteSimulationsPage().then((module) => ({ default: module.NoteSimulationsPage })),
);
const CalendarPage = lazy(() => loadCalendarPage().then((module) => ({ default: module.CalendarPage })));
const LearningPage = lazy(() => loadLearningPage().then((module) => ({ default: module.LearningPage })));
const AdminPortal = lazy(() => import("./pages/AdminPortal").then((module) => ({ default: module.AdminPortal })));
const TrustPage = lazy(() => import("./pages/TrustPage").then((module) => ({ default: module.TrustPage })));
const DemoPage = lazy(() => import("./pages/DemoPage").then((module) => ({ default: module.DemoPage })));

const studentRouteLoaders: Record<string, () => Promise<unknown>> = {
  "/": loadOverviewPage,
  "/results": loadResultsPages,
  "/calendar": loadCalendarPage,
  "/ues/releve": loadAcademicReportPage,
  "/leaderboard": loadLeaderboardPage,
  "/simulations": loadSimulationsPage,
  "/simulations/gpa": loadSimulationsPage,
  "/simulations/notes": loadNoteSimulationsPage,
  "/sharing": loadSharingPage,
  "/settings": loadSettingsPage,
  "/parcours": loadLearningPage,
};

const documentTitles: Record<string, string> = {
  "/": "Vue d'ensemble",
  "/results": "Résultats",
  "/calendar": "Agenda",
  "/ues/releve": "Relevé académique",
  "/leaderboard": "Classement",
  "/simulations": "Simulations",
  "/sharing": "Partage",
  "/settings": "Paramètres",
  "/confiance": "Confiance",
  "/demo": "Démo",
  "/admin": "Administration",
};

function preloadStudentRoute(path: string) {
  void studentRouteLoaders[path]?.();
}

function studentDocumentTitle(path: string): string | undefined {
  if (path.startsWith("/results")) return "Résultats";
  if (path.startsWith("/simulations")) return "Simulations";
  if (path.startsWith("/parcours")) return learningDocumentTitle(path);
  return documentTitles[path];
}

function LegacyResultsRedirect({ view }: { view: "ues" | "evaluations" }) {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  params.set("view", view);
  return <Navigate to={`/results?${params.toString()}`} replace />;
}

export default function App() {
  const location = useLocation();

  useEffect(() => {
    const route = location.pathname.startsWith("/admin") ? "/admin" : location.pathname;
    if (route !== "/admin" && route !== "/confiance" && route !== "/demo") return;
    const title = documentTitles[route];
    document.title = title ? `${title} · IMTégrale` : "IMTégrale";
  }, [location.pathname]);

  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  if (location.pathname.startsWith("/admin")) {
    return (
      <Suspense fallback={<div className="route-loading skeleton" />}>
        <AdminPortal />
      </Suspense>
    );
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
  const location = useLocation();
  const session = useSession();
  const queryClient = useQueryClient();
  const authenticated = Boolean(session.data?.authenticated);

  useEffect(() => {
    const title = authenticated ? studentDocumentTitle(location.pathname) : "Connexion";
    document.title = title ? `${title} · IMTégrale` : "IMTégrale";
  }, [authenticated, location.pathname]);

  useLayoutEffect(() => {
    if (authenticated) window.scrollTo(0, 0);
  }, [authenticated]);

  useEffect(() => {
    const handleUnauthorized = () => {
      replaceSessionState(queryClient, { authenticated: false });
      broadcastSessionChange();
    };
    window.addEventListener("botnote:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("botnote:unauthorized", handleUnauthorized);
  }, [queryClient]);

  useEffect(() => {
    const handleLearningReverification = () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.session });
    };
    window.addEventListener("botnote:learning-reverify", handleLearningReverification);
    return () => window.removeEventListener("botnote:learning-reverify", handleLearningReverification);
  }, [queryClient]);

  useEffect(
    () =>
      subscribeToSessionChanges(() => {
        clearAccountState(queryClient);
        queryClient.invalidateQueries({ queryKey: queryKeys.session });
      }),
    [queryClient],
  );

  if (session.isPending) {
    return (
      <div className="app-loading">
        <Logo />
        <span className="loading-line" />
      </div>
    );
  }
  if (!session.data?.authenticated || !session.data.account || !session.data.role) {
    return <LoginPage />;
  }

  const isOwner = session.data.role === "owner";
  const isPrimaryOwner = isPrimaryOwnerSession(session.data);
  return (
    <>
      <Routes>
        <Route element={<AppShell session={session.data} preloadRoute={preloadStudentRoute} />}>
          <Route index element={<OverviewPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="results/ue/:ueCode" element={<ResultsUeDetailPage />} />
          <Route path="notes" element={<LegacyResultsRedirect view="evaluations" />} />
          <Route path="calendar" element={isPrimaryOwner ? <CalendarPage /> : <Navigate to="/" replace />} />
          <Route
            path="ues/releve"
            element={isPrimaryOwner ? <AcademicReportPage /> : <Navigate to="/results?view=ues" replace />}
          />
          <Route path="ues" element={<LegacyResultsRedirect view="ues" />} />
          <Route path="leaderboard" element={isOwner ? <LeaderboardPage /> : <Navigate to="/" replace />} />
          <Route path="simulations" element={isPrimaryOwner ? <SimulationLayout /> : <Navigate to="/" replace />}>
            <Route index element={<Navigate to="gpa" replace />} />
            <Route path="gpa" element={<SimulationsPage />} />
            <Route path="notes" element={<NoteSimulationsPage />} />
          </Route>
          <Route path="sharing" element={isOwner ? <SharingPage /> : <Navigate to="/" replace />} />
          <Route path="parcours/*" element={<LearningPage session={session.data} />} />
          <Route path="settings" element={<SettingsPage role={session.data.role} isPrimaryOwner={isPrimaryOwner} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <SecuritySetupModal
        open={Boolean(isPrimaryOwner && session.data.needs_security_setup)}
        isPrimaryOwner={isPrimaryOwner}
        onComplete={() => queryClient.invalidateQueries({ queryKey: queryKeys.session })}
      />
      <SyncSetupModal
        open={Boolean(isPrimaryOwner && !session.data.needs_security_setup && session.data.needs_sync_setup)}
        onComplete={() => queryClient.invalidateQueries({ queryKey: queryKeys.session })}
      />
    </>
  );
}
