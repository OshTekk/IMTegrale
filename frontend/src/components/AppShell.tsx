import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Ellipsis, LibraryBig, LogIn, LogOut, RefreshCw, ShieldCheck } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../lib/api";
import { isPrimaryOwnerSession } from "../lib/auth";
import { learningEntryVisible } from "../lib/learning";
import { isCurrentLogoutAttempt, requestServerConfirmedLogout, type LogoutPhase } from "../lib/logout";
import { replaceSessionState, useDashboard, useRefreshDashboard } from "../lib/queries";
import { broadcastSessionChange } from "../lib/sessionSync";
import { formatSyncDuration, manualSyncMessage, useServerCountdown } from "../lib/sync";
import { useAccountEventStream } from "../lib/useAccountEventStream";
import type { Session } from "../types";
import { Logo } from "./Logo";
import { Modal } from "./Modal";
import { PassReconnectModal } from "./PassReconnectModal";
import { SourceNotice } from "./SourceNotice";
import { ThemeToggle } from "./ThemeToggle";
import { useToast } from "./Toast";
import {
  appPageHeading,
  appPageTitles,
  isAppNavItemActive,
  mobileAppNavigation,
  mobileNavDescriptions,
  visibleAppNavigation,
} from "./appNavigation";

export { isAppNavItemActive } from "./appNavigation";

function PageRouteLoading() {
  return (
    <div className="page-route-loading" role="status" aria-busy="true" aria-label="Chargement de la page">
      <div className="skeleton route-heading-skeleton" />
      <div className="skeleton route-content-skeleton" />
    </div>
  );
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
  const [logoutIssue, setLogoutIssue] = useState<"failed" | "indeterminate" | null>(null);
  const [logoutPhase, setLogoutPhase] = useState<LogoutPhase>("idle");
  const [title, subtitle] = appPageHeading(location.pathname, session);
  const primaryOwner = isPrimaryOwnerSession(session);
  const visibleNav = visibleAppNavigation(session, primaryOwner);
  const { primary: mobilePrimaryNav, secondary: mobileSecondaryNav } = mobileAppNavigation(session, primaryOwner);
  const profileWrap = useRef<HTMLDivElement>(null);
  const logoutRetryButton = useRef<HTMLButtonElement>(null);
  const logoutAttempt = useRef(0);
  const logoutPending = useRef(false);
  const logoutMounted = useRef(true);
  const mobileSecondaryFirstLink = useRef<HTMLAnchorElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const previousPath = useRef(location.pathname);
  const manualSync = dashboard.data?.account.manual_sync;
  const syncRemaining = useServerCountdown(manualSync);
  const syncMessage = manualSyncMessage(manualSync, syncRemaining);
  const syncRecheckKey = useRef<string | null>(null);
  const eventAccountId = dashboard.data?.account.id;
  const latestEventId = dashboard.data?.latest_event_id;
  const live = useAccountEventStream(eventAccountId, latestEventId);

  useEffect(() => {
    logoutMounted.current = true;
    return () => {
      logoutMounted.current = false;
      logoutAttempt.current += 1;
    };
  }, []);

  useEffect(() => {
    setProfileOpen(false);
    setMobileMenuOpen(false);
    if (previousPath.current !== location.pathname) {
      previousPath.current = location.pathname;
      window.requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
    }
  }, [location.pathname]);

  useEffect(() => {
    if (!profileOpen) return;
    const close = (event: PointerEvent) => {
      if (!profileWrap.current?.contains(event.target as Node)) setProfileOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !document.querySelector("[aria-modal='true']")) setProfileOpen(false);
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
      },
    });
  };

  const syncButtonLabel =
    manualSync?.state === "in_progress"
      ? "En cours"
      : manualSync?.state === "cooldown" || manualSync?.state === "pass_unavailable"
        ? syncRemaining > 0
          ? formatSyncDuration(syncRemaining)
          : "Vérification"
        : manualSync?.state === "reauth_required"
          ? "Reconnecter"
          : "Synchroniser";

  const finalizeAuthoritativeSessionChange = async (authoritativeSession: Session) => {
    await queryClient.cancelQueries();
    queryClient.clear();
    replaceSessionState(queryClient, authoritativeSession);
    broadcastSessionChange();
    if (!authoritativeSession.authenticated) navigate("/");
    window.location.reload();
  };

  const logout = async () => {
    if (logoutPending.current || !session.account) return;
    logoutPending.current = true;
    setLogoutIssue(null);
    const attempt = ++logoutAttempt.current;
    setLogoutPhase("requesting");

    try {
      const result = await requestServerConfirmedLogout({
        expectedAccountId: session.account.id,
        onPhase: (phase) => {
          if (!logoutMounted.current || !isCurrentLogoutAttempt(attempt, logoutAttempt.current)) return;
          setLogoutPhase(phase);
        },
      });
      if (!logoutMounted.current || !isCurrentLogoutAttempt(attempt, logoutAttempt.current)) return;

      if (result.kind === "confirmed") {
        await finalizeAuthoritativeSessionChange({ authenticated: false });
        return;
      }
      if (result.kind === "principal-changed") {
        await finalizeAuthoritativeSessionChange(result.session);
        return;
      }
      setLogoutIssue(result.kind);
    } finally {
      if (attempt === logoutAttempt.current) logoutPending.current = false;
    }
  };

  const closeLogoutIssue = () => {
    setLogoutIssue(null);
    setLogoutPhase("idle");
  };

  const logoutIsBusy =
    logoutPending.current || logoutPhase === "requesting" || logoutPhase === "verifying" || logoutPhase === "confirmed";
  const logoutButtonLabel =
    logoutPhase === "verifying"
      ? "Vérification de la session…"
      : logoutPhase === "requesting"
        ? "Déconnexion…"
        : "Se déconnecter";

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Aller au contenu
      </a>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Logo />
        </div>
        <nav className="sidebar-nav" aria-label="Navigation principale">
          {visibleNav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              viewTransition
              onMouseEnter={() => preloadRoute(item.to)}
              onFocus={() => preloadRoute(item.to)}
              className={() => (isAppNavItemActive(item.to, location.pathname) ? "active" : "")}
            >
              <item.icon size={19} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        {session.role !== "owner" && (
          <div className="access-badge">
            <ShieldCheck size={17} />
            <div>
              <strong>Lecture seule</strong>
              <span>Accès partagé</span>
            </div>
          </div>
        )}
        <div className="sidebar-status" role="status" aria-live="polite">
          <span className={`live-dot ${live}`} />
          <div>
            <strong>{live === "connected" ? "Données en direct" : "Reconnexion…"}</strong>
            <span>
              {dashboard.data?.account.last_sync_at
                ? `Sync ${new Date(dashboard.data.account.last_sync_at).toLocaleDateString("fr-FR")}`
                : "En attente de sync"}
            </span>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="page-heading">
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
          <div className="topbar-actions">
            {primaryOwner && (
              <button
                className={`secondary-button sync-button${manualSync?.state === "reauth_required" ? " is-reauth-required" : ""}`}
                type="button"
                onClick={runSync}
                disabled={sync.isPending || (!manualSync?.can_start && manualSync?.state !== "reauth_required")}
                aria-label={syncMessage}
                title={syncMessage}
              >
                {manualSync?.state === "reauth_required" ? (
                  <LogIn size={18} />
                ) : (
                  <RefreshCw
                    size={17}
                    className={sync.isPending || manualSync?.state === "in_progress" ? "spin" : ""}
                  />
                )}
                <span>{syncButtonLabel}</span>
              </button>
            )}
            <div className="profile-wrap" ref={profileWrap}>
              <button
                className="profile-button"
                type="button"
                onClick={() => setProfileOpen((value) => !value)}
                aria-expanded={profileOpen}
                aria-controls="profile-menu"
                aria-haspopup="menu"
                aria-label={`Ouvrir le profil de ${session.account?.display_name ?? "l'utilisateur"}`}
              >
                <span className="avatar">{session.account?.display_name.slice(0, 2).toUpperCase()}</span>
                <span className="profile-copy">
                  <strong>{session.account?.display_name}</strong>
                  <small>
                    {session.auth_method === "imt"
                      ? "Compte IMT"
                      : session.auth_method === "passkey"
                        ? "Passkey"
                        : "Accès partagé"}
                  </small>
                </span>
                <ChevronDown className="profile-chevron" size={15} aria-hidden="true" />
              </button>
              {profileOpen && (
                <div className="profile-menu" id="profile-menu" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => void logout()}
                    disabled={logoutIsBusy}
                    aria-busy={logoutIsBusy}
                  >
                    <LogOut size={17} /> {logoutButtonLabel}
                  </button>
                </div>
              )}
            </div>
            <ThemeToggle />
            {learningEntryVisible(session) && (
              <NavLink
                className="secondary-button learning-shell-cta"
                to="/parcours"
                aria-label={location.pathname.startsWith("/parcours") ? "Continuer mon parcours" : "Réussir ma 2A"}
                onMouseEnter={() => preloadRoute("/parcours")}
                onFocus={() => preloadRoute("/parcours")}
              >
                <LibraryBig size={17} />
                <span>{location.pathname.startsWith("/parcours") ? "Continuer mon parcours" : "Réussir ma 2A"}</span>
              </NavLink>
            )}
          </div>
        </header>
        <main ref={mainRef} className="page-content" id="main-content" tabIndex={-1}>
          <Suspense fallback={<PageRouteLoading />}>
            <Outlet />
          </Suspense>
        </main>
        <footer className="product-footer">
          <SourceNotice compact />
        </footer>
      </div>

      <nav className="mobile-nav" aria-label="Navigation mobile">
        {mobilePrimaryNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            viewTransition
            onTouchStart={() => preloadRoute(item.to)}
            onFocus={() => preloadRoute(item.to)}
            aria-current={isAppNavItemActive(item.to, location.pathname) ? "page" : undefined}
            className={isAppNavItemActive(item.to, location.pathname) ? "active" : ""}
          >
            <item.icon size={20} />
            <span>{item.short}</span>
          </NavLink>
        ))}
        {mobileSecondaryNav.length > 0 && (
          <button
            className={
              mobileSecondaryNav.some((item) => isAppNavItemActive(item.to, location.pathname)) ? "active" : ""
            }
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            aria-label="Ouvrir les autres pages"
            aria-expanded={mobileMenuOpen}
            aria-controls="mobile-overflow-navigation"
            aria-current={
              mobileSecondaryNav.some((item) => isAppNavItemActive(item.to, location.pathname)) ? "page" : undefined
            }
          >
            <Ellipsis size={21} />
            <span>Plus</span>
          </button>
        )}
      </nav>

      <Modal
        open={mobileMenuOpen}
        title="Autres pages"
        description="Retrouve les pages disponibles pour ton compte."
        onClose={() => setMobileMenuOpen(false)}
        initialFocusRef={mobileSecondaryFirstLink}
      >
        <nav className="mobile-overflow-links" id="mobile-overflow-navigation" aria-label="Navigation secondaire">
          {mobileSecondaryNav.map((item, index) => (
            <NavLink
              key={item.to}
              ref={index === 0 ? mobileSecondaryFirstLink : undefined}
              to={item.to}
              viewTransition
              onTouchStart={() => preloadRoute(item.to)}
              onFocus={() => preloadRoute(item.to)}
              onClick={() => setMobileMenuOpen(false)}
              aria-current={isAppNavItemActive(item.to, location.pathname) ? "page" : undefined}
              className={isAppNavItemActive(item.to, location.pathname) ? "active" : ""}
            >
              <item.icon size={19} />
              <span>
                <strong>{item.label}</strong>
                <small>{mobileNavDescriptions[item.to] ?? appPageTitles[item.to]?.[1]}</small>
              </span>
            </NavLink>
          ))}
        </nav>
      </Modal>
      <Modal
        open={logoutIssue !== null}
        title={logoutIssue === "failed" ? "Déconnexion non confirmée" : "Impossible de confirmer la déconnexion"}
        description={
          logoutIssue === "failed"
            ? "La session est encore active. Réessaie dans un instant."
            : "L’état de la session n’a pas pu être vérifié. Réessaie ou recharge la page avant de quitter cet appareil."
        }
        onClose={closeLogoutIssue}
        initialFocusRef={logoutRetryButton}
        size="small"
      >
        <footer className="modal-actions">
          {logoutIssue === "indeterminate" && (
            <button className="secondary-button" type="button" onClick={() => window.location.reload()}>
              Recharger la page
            </button>
          )}
          <button ref={logoutRetryButton} className="primary-button" type="button" onClick={() => void logout()}>
            Réessayer
          </button>
        </footer>
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
