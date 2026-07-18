import { useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, Ellipsis, Gauge, KeyRound, LogOut, NotebookPen, RefreshCw, Settings, ShieldCheck, Trophy } from "lucide-react";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { queryKeys, useDashboard, useRefreshDashboard } from "../lib/queries";
import { broadcastSessionChange } from "../lib/sessionSync";
import { formatSyncDuration, manualSyncMessage, useServerCountdown } from "../lib/sync";
import type { Session } from "../types";
import { Logo } from "./Logo";
import { Modal } from "./Modal";
import { SourceNotice } from "./SourceNotice";
import { ThemeToggle } from "./ThemeToggle";
import { useToast } from "./Toast";

const navItems = [
  { to: "/", label: "Vue d'ensemble", short: "Accueil", icon: Gauge, ownerOnly: false },
  { to: "/notes", label: "Notes", short: "Notes", icon: NotebookPen, ownerOnly: false },
  { to: "/ues", label: "UE & ECTS", short: "UE", icon: BookOpenCheck, ownerOnly: false },
  { to: "/leaderboard", label: "Classement", short: "Rangs", icon: Trophy, ownerOnly: true },
  { to: "/sharing", label: "Partage", short: "Partage", icon: KeyRound, ownerOnly: true },
  { to: "/settings", label: "Paramètres", short: "Réglages", icon: Settings, ownerOnly: false }
];

const titles: Record<string, [string, string]> = {
  "/": ["Vue d'ensemble", "Ta situation académique en un coup d'œil"],
  "/notes": ["Notes", "Détail des évaluations officielles importées depuis PASS"],
  "/ues": ["UE & ECTS", "Moyennes, grades et pondération par crédits"],
  "/leaderboard": ["Classement", "Deux classements privés, calculés depuis PASS"],
  "/sharing": ["Partage", "Accès révocables liés à ton compte"],
  "/settings": ["Paramètres", "Compte, synchronisation et notifications"]
};

function PageRouteLoading() {
  return <div className="page-route-loading" aria-busy="true" aria-label="Chargement de la page"><div className="skeleton route-heading-skeleton" /><div className="skeleton route-content-skeleton" /></div>;
}

export function AppShell({ session, preloadRoute }: { session: Session; preloadRoute: (path: string) => void }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const dashboard = useDashboard();
  const sync = useRefreshDashboard();
  const { showToast } = useToast();
  const [profileOpen, setProfileOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [live, setLive] = useState<"connected" | "connecting">("connecting");
  const [title, subtitle] = titles[location.pathname] ?? titles["/"]!;
  const visibleNav = useMemo(() => navItems.filter((item) => !item.ownerOnly || session.role === "owner"), [session.role]);
  const mobilePrimaryNav = useMemo(() => visibleNav.filter((item) => !["/sharing", "/settings"].includes(item.to)), [visibleNav]);
  const mobileSecondaryNav = useMemo(() => visibleNav.filter((item) => ["/sharing", "/settings"].includes(item.to)), [visibleNav]);
  const profileWrap = useRef<HTMLDivElement>(null);
  const manualSync = dashboard.data?.account.manual_sync;
  const syncRemaining = useServerCountdown(manualSync);
  const syncMessage = manualSyncMessage(manualSync, syncRemaining);
  const syncRecheckKey = useRef<string | null>(null);

  useEffect(() => {
    if (!dashboard.data) return;
    const source = new EventSource(`/api/v1/events?after=${dashboard.data.latest_event_id}`);
    source.onopen = () => setLive("connected");
    source.onerror = () => setLive("connecting");
    source.addEventListener("update", () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.account });
    });
    source.addEventListener("unauthorized", () => {
      source.close();
      window.dispatchEvent(new CustomEvent("botnote:unauthorized"));
    });
    return () => source.close();
  }, [dashboard.data?.latest_event_id, queryClient]);

  useEffect(() => {
    setProfileOpen(false);
    setMobileMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!profileOpen) return;
    const close = (event: PointerEvent) => {
      if (!profileWrap.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setProfileOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [profileOpen]);

  useEffect(() => {
    if (!manualSync || manualSync.can_start || syncRemaining > 0) {
      if (manualSync?.can_start) syncRecheckKey.current = null;
      return;
    }
    const key = `${manualSync.state}:${manualSync.server_time}`;
    if (syncRecheckKey.current === key) return;
    syncRecheckKey.current = key;
    void dashboard.refetch();
  }, [dashboard, manualSync, syncRemaining]);

  const runSync = () => {
    sync.mutate(undefined, {
      onSuccess: () => showToast("Synchronisation lancée"),
      onError: (error) => showToast(error.message, "error")
    });
  };

  const syncButtonLabel = manualSync?.state === "in_progress"
    ? "En cours"
    : manualSync?.state === "cooldown" || manualSync?.state === "pass_unavailable"
      ? syncRemaining > 0 ? formatSyncDuration(syncRemaining) : "Vérification"
      : "Synchroniser";

  const logout = async () => {
    await api("/api/v1/auth/logout", { method: "POST", body: "{}" }).catch(() => undefined);
    queryClient.clear();
    broadcastSessionChange();
    navigate("/");
    window.location.reload();
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><Logo /></div>
        <nav className="sidebar-nav" aria-label="Navigation principale">
          {visibleNav.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"} viewTransition onMouseEnter={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} className={({ isActive }) => isActive ? "active" : ""}><item.icon size={19} /><span>{item.label}</span></NavLink>)}
        </nav>
        {session.role !== "owner" && <div className="access-badge"><ShieldCheck size={17} /><div><strong>Lecture seule</strong><span>Accès partagé</span></div></div>}
        <div className="sidebar-status"><span className={`live-dot ${live}`} /><div><strong>{live === "connected" ? "Données en direct" : "Reconnexion…"}</strong><span>{dashboard.data?.account.last_sync_at ? `Sync ${new Date(dashboard.data.account.last_sync_at).toLocaleDateString("fr-FR")}` : "En attente de sync"}</span></div></div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="page-heading"><h1>{title}</h1><p>{subtitle}</p></div>
          <div className="topbar-actions">
            {session.role === "owner" && <button className="secondary-button sync-button" type="button" onClick={runSync} disabled={sync.isPending || !manualSync?.can_start} aria-label={syncMessage} title={syncMessage}><RefreshCw size={17} className={sync.isPending || manualSync?.state === "in_progress" ? "spin" : ""} /><span>{syncButtonLabel}</span></button>}
            <ThemeToggle />
            <div className="profile-wrap" ref={profileWrap}>
              <button className="profile-button" type="button" onClick={() => setProfileOpen((value) => !value)} aria-expanded={profileOpen} aria-label={`Ouvrir le profil de ${session.account?.display_name ?? "l'utilisateur"}`}>
                <span className="avatar">{session.account?.display_name.slice(0, 2).toUpperCase()}</span>
                <span className="profile-copy"><strong>{session.account?.display_name}</strong><small>{session.auth_method === "imt" ? "Compte IMT" : "Accès partagé"}</small></span>
              </button>
              {profileOpen && <div className="profile-menu"><button type="button" onClick={logout}><LogOut size={17} /> Se déconnecter</button></div>}
            </div>
          </div>
        </header>
        <main className="page-content"><Suspense fallback={<PageRouteLoading />}><Outlet /></Suspense></main>
        <footer className="product-footer"><SourceNotice compact /></footer>
      </div>

      <nav className="mobile-nav" aria-label="Navigation mobile">
        {mobilePrimaryNav.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"} viewTransition onTouchStart={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} className={({ isActive }) => isActive ? "active" : ""}><item.icon size={20} /><span>{item.short}</span></NavLink>)}
        {mobileSecondaryNav.length > 0 && <button className={mobileSecondaryNav.some((item) => item.to === location.pathname) ? "active" : ""} type="button" onClick={() => setMobileMenuOpen(true)} aria-label="Ouvrir les autres pages" aria-expanded={mobileMenuOpen}><Ellipsis size={21} /><span>Plus</span></button>}
      </nav>

      <Modal open={mobileMenuOpen} title="Autres pages" description="Compte, accès et préférences." onClose={() => setMobileMenuOpen(false)}>
        <nav className="mobile-overflow-links" aria-label="Navigation secondaire">{mobileSecondaryNav.map((item) => <NavLink key={item.to} to={item.to} viewTransition onTouchStart={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} onClick={() => setMobileMenuOpen(false)}><item.icon size={19} /><span><strong>{item.label}</strong><small>{item.to === "/sharing" ? "Tokens et accès partagés" : "Compte, synchronisation et notifications"}</small></span></NavLink>)}</nav>
      </Modal>
    </div>
  );
}
