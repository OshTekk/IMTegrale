import { useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, CalendarDays, ChevronDown, Ellipsis, FlaskConical, Gauge, KeyRound, LogOut, NotebookPen, RefreshCw, Settings, ShieldCheck, Trophy } from "lucide-react";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { eventReconnectDelay } from "../lib/events";
import { queryKeys, useDashboard, useRefreshDashboard } from "../lib/queries";
import { broadcastSessionChange } from "../lib/sessionSync";
import { formatSyncDuration, manualSyncMessage, useServerCountdown } from "../lib/sync";
import type { Session } from "../types";
import { Logo } from "./Logo";
import { Modal } from "./Modal";
import { PassReconnectModal } from "./PassReconnectModal";
import { SourceNotice } from "./SourceNotice";
import { ThemeToggle } from "./ThemeToggle";
import { useToast } from "./Toast";

const navItems = [
  { to: "/", label: "Vue d'ensemble", short: "Accueil", icon: Gauge, ownerOnly: false },
  { to: "/notes", label: "Notes", short: "Notes", icon: NotebookPen, ownerOnly: false },
  { to: "/calendar", label: "Agenda", short: "Agenda", icon: CalendarDays, ownerOnly: true, primaryOwnerOnly: true },
  { to: "/ues", label: "UE & ECTS", short: "UE", icon: BookOpenCheck, ownerOnly: false },
  { to: "/simulations/gpa", label: "Simulations", short: "Simuler", icon: FlaskConical, ownerOnly: true, primaryOwnerOnly: true },
  { to: "/leaderboard", label: "Classement", short: "Rangs", icon: Trophy, ownerOnly: true },
  { to: "/sharing", label: "Partage", short: "Partage", icon: KeyRound, ownerOnly: true },
  { to: "/settings", label: "Paramètres", short: "Réglages", icon: Settings, ownerOnly: false }
];

const titles: Record<string, [string, string]> = {
  "/": ["Vue d'ensemble", "Ta situation académique en un coup d'œil"],
  "/notes": ["Notes", "Détail des évaluations officielles importées depuis PASS"],
  "/calendar": ["Agenda", "Tes cours et ton rythme de formation au même endroit"],
  "/ues": ["UE & ECTS", "Moyennes, grades et pondération par crédits"],
  "/ues/releve": ["Relevé académique", "Prépare un document personnel clair et partageable"],
  "/simulations/gpa": ["Simulations", "Projette ton GPA sans modifier tes données officielles"],
  "/simulations/notes": ["Simulations", "Teste tes prochaines notes et leur impact sur ta moyenne"],
  "/leaderboard": ["Classement", "Deux classements privés, calculés depuis PASS"],
  "/sharing": ["Partage", "Accès révocables liés à ton compte"],
  "/settings": ["Paramètres", "Compte, synchronisation et notifications"]
};

const mobileNavDescriptions: Record<string, string> = {
  "/ues": "Grades, crédits et détail des évaluations",
  "/calendar": "Cours personnels et calendrier de formation",
  "/sharing": "Tokens et accès partagés",
  "/settings": "Compte, synchronisation et notifications"
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
  const [passReconnectOpen, setPassReconnectOpen] = useState(false);
  const [live, setLive] = useState<"connected" | "connecting">("connecting");
  const [title, subtitle] = titles[location.pathname] ?? titles["/"]!;
  const primaryOwner = session.role === "owner" && session.auth_method !== "token";
  const visibleNav = useMemo(() => navItems.filter((item) => (
    (!item.ownerOnly || session.role === "owner") && (!item.primaryOwnerOnly || primaryOwner)
  )), [primaryOwner, session.role]);
  const mobilePrimaryPaths = useMemo(() => primaryOwner
    ? ["/", "/notes", "/calendar", "/simulations/gpa"]
    : ["/", "/notes", "/ues"], [primaryOwner]);
  const mobilePrimaryNav = useMemo(() => visibleNav.filter((item) => mobilePrimaryPaths.includes(item.to)), [mobilePrimaryPaths, visibleNav]);
  const mobileSecondaryNav = useMemo(() => visibleNav.filter((item) => !mobilePrimaryPaths.includes(item.to)), [mobilePrimaryPaths, visibleNav]);
  const profileWrap = useRef<HTMLDivElement>(null);
  const manualSync = dashboard.data?.account.manual_sync;
  const syncRemaining = useServerCountdown(manualSync);
  const syncMessage = manualSyncMessage(manualSync, syncRemaining);
  const syncRecheckKey = useRef<string | null>(null);
  const eventCursor = useRef({ accountId: "", lastId: 0 });
  const eventAccountId = dashboard.data?.account.id;
  const latestEventId = dashboard.data?.latest_event_id;

  useEffect(() => {
    if (!eventAccountId || latestEventId === undefined) return;
    if (eventCursor.current.accountId !== eventAccountId) {
      eventCursor.current = { accountId: eventAccountId, lastId: latestEventId };
      return;
    }
    eventCursor.current.lastId = Math.max(eventCursor.current.lastId, latestEventId);
  }, [eventAccountId, latestEventId]);

  useEffect(() => {
    if (!eventAccountId) return;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let retryAttempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/v1/events?after=${eventCursor.current.lastId}`);
      source.onopen = () => {
        retryAttempt = 0;
        setLive("connected");
      };
      source.onerror = () => {
        source?.close();
        setLive("connecting");
        if (stopped) return;
        const delay = eventReconnectDelay(retryAttempt);
        retryAttempt += 1;
        retryTimer = window.setTimeout(connect, delay);
      };
      source.addEventListener("update", (event) => {
        const eventId = Number((event as MessageEvent).lastEventId);
        if (Number.isFinite(eventId) && eventId > 0) {
          eventCursor.current.lastId = Math.max(eventCursor.current.lastId, eventId);
        }
        queryClient.invalidateQueries({ queryKey: queryKeys.account });
      });
      source.addEventListener("unauthorized", () => {
        stopped = true;
        source?.close();
        window.dispatchEvent(new CustomEvent("botnote:unauthorized"));
      });
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (retryTimer !== null) window.clearTimeout(retryTimer);
    };
  }, [eventAccountId, queryClient]);

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
    if (manualSync?.state === "reauth_required") {
      setPassReconnectOpen(true);
      return;
    }
    sync.mutate(undefined, {
      onSuccess: () => showToast("Synchronisation lancée"),
      onError: (error) => {
        if (error instanceof ApiError && error.code === "SYNC_REAUTH_REQUIRED") {
          setPassReconnectOpen(true);
          return;
        }
        showToast(error.message, "error");
      }
    });
  };

  const syncButtonLabel = manualSync?.state === "in_progress"
    ? "En cours"
    : manualSync?.state === "cooldown" || manualSync?.state === "pass_unavailable"
      ? syncRemaining > 0 ? formatSyncDuration(syncRemaining) : "Vérification"
      : manualSync?.state === "reauth_required" ? "Reconnecter" : "Synchroniser";

  const logout = async () => {
    await api("/api/v1/auth/logout", { method: "POST", body: "{}" }).catch(() => undefined);
    queryClient.clear();
    broadcastSessionChange();
    navigate("/");
    window.location.reload();
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Aller au contenu</a>
      <aside className="sidebar">
        <div className="sidebar-brand"><Logo /></div>
        <nav className="sidebar-nav" aria-label="Navigation principale">
          {visibleNav.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"} viewTransition onMouseEnter={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} className={({ isActive }) => isActive || (item.to.startsWith("/simulations") && location.pathname.startsWith("/simulations")) ? "active" : ""}><item.icon size={19} /><span>{item.label}</span></NavLink>)}
        </nav>
        {session.role !== "owner" && <div className="access-badge"><ShieldCheck size={17} /><div><strong>Lecture seule</strong><span>Accès partagé</span></div></div>}
        <div className="sidebar-status" role="status" aria-live="polite"><span className={`live-dot ${live}`} /><div><strong>{live === "connected" ? "Données en direct" : "Reconnexion…"}</strong><span>{dashboard.data?.account.last_sync_at ? `Sync ${new Date(dashboard.data.account.last_sync_at).toLocaleDateString("fr-FR")}` : "En attente de sync"}</span></div></div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="page-heading"><h1>{title}</h1><p>{subtitle}</p></div>
          <div className="topbar-actions">
            {session.role === "owner" && <button className="secondary-button sync-button" type="button" onClick={runSync} disabled={sync.isPending || (!manualSync?.can_start && manualSync?.state !== "reauth_required")} aria-label={syncMessage} title={syncMessage}><RefreshCw size={17} className={sync.isPending || manualSync?.state === "in_progress" ? "spin" : ""} /><span>{syncButtonLabel}</span></button>}
            <ThemeToggle />
            <div className="profile-wrap" ref={profileWrap}>
              <button className="profile-button" type="button" onClick={() => setProfileOpen((value) => !value)} aria-expanded={profileOpen} aria-controls="profile-menu" aria-haspopup="menu" aria-label={`Ouvrir le profil de ${session.account?.display_name ?? "l'utilisateur"}`}>
                <span className="avatar">{session.account?.display_name.slice(0, 2).toUpperCase()}</span>
                <span className="profile-copy"><strong>{session.account?.display_name}</strong><small>{session.auth_method === "imt" ? "Compte IMT" : "Accès partagé"}</small></span>
                <ChevronDown className="profile-chevron" size={15} aria-hidden="true" />
              </button>
              {profileOpen && <div className="profile-menu" id="profile-menu" role="menu"><button type="button" role="menuitem" onClick={logout}><LogOut size={17} /> Se déconnecter</button></div>}
            </div>
          </div>
        </header>
        <main className="page-content" id="main-content"><Suspense fallback={<PageRouteLoading />}><Outlet /></Suspense></main>
        <footer className="product-footer"><SourceNotice compact /></footer>
      </div>

      <nav className="mobile-nav" aria-label="Navigation mobile">
        {mobilePrimaryNav.map((item) => <NavLink key={item.to} to={item.to} end={item.to === "/"} viewTransition onTouchStart={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} className={({ isActive }) => isActive || (item.to.startsWith("/simulations") && location.pathname.startsWith("/simulations")) ? "active" : ""}><item.icon size={20} /><span>{item.short}</span></NavLink>)}
        {mobileSecondaryNav.length > 0 && <button className={mobileSecondaryNav.some((item) => item.to === location.pathname) ? "active" : ""} type="button" onClick={() => setMobileMenuOpen(true)} aria-label="Ouvrir les autres pages" aria-expanded={mobileMenuOpen}><Ellipsis size={21} /><span>Plus</span></button>}
      </nav>

      <Modal open={mobileMenuOpen} title="Autres pages" description="Données académiques, accès et préférences." onClose={() => setMobileMenuOpen(false)}>
        <nav className="mobile-overflow-links" aria-label="Navigation secondaire">{mobileSecondaryNav.map((item) => <NavLink key={item.to} to={item.to} viewTransition onTouchStart={() => preloadRoute(item.to)} onFocus={() => preloadRoute(item.to)} onClick={() => setMobileMenuOpen(false)}><item.icon size={19} /><span><strong>{item.label}</strong><small>{mobileNavDescriptions[item.to] ?? titles[item.to]?.[1]}</small></span></NavLink>)}</nav>
      </Modal>
      <PassReconnectModal
        open={passReconnectOpen}
        identifier={session.account?.imt_username}
        onClose={() => setPassReconnectOpen(false)}
        onRenewed={() => {
          sync.mutate(undefined, {
            onSuccess: () => showToast("Synchronisation lancée"),
            onError: (error) => showToast(error.message, "error"),
          });
        }}
      />
    </div>
  );
}
