import {
  BookOpenCheck,
  CalendarDays,
  FlaskConical,
  Gauge,
  KeyRound,
  LibraryBig,
  Settings,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import { learningEntryVisible } from "../lib/learning";
import { readerAudienceSubtitle } from "../lib/learningPresentation";
import type { Session } from "../types";

export interface AppNavItem {
  to: string;
  label: string;
  short: string;
  icon: LucideIcon;
  ownerOnly: boolean;
  primaryOwnerOnly?: boolean;
  learningOnly?: boolean;
}

export const appNavItems: AppNavItem[] = [
  { to: "/", label: "Vue d'ensemble", short: "Accueil", icon: Gauge, ownerOnly: false },
  { to: "/results", label: "Résultats", short: "Résultats", icon: BookOpenCheck, ownerOnly: false },
  { to: "/calendar", label: "Agenda", short: "Agenda", icon: CalendarDays, ownerOnly: true, primaryOwnerOnly: true },
  { to: "/parcours", label: "Parcours", short: "Parcours", icon: LibraryBig, ownerOnly: false, learningOnly: true },
  {
    to: "/simulations/gpa",
    label: "Simulations",
    short: "Simuler",
    icon: FlaskConical,
    ownerOnly: true,
    primaryOwnerOnly: true,
  },
  { to: "/leaderboard", label: "Classement", short: "Rangs", icon: Trophy, ownerOnly: true },
  { to: "/sharing", label: "Partage", short: "Partage", icon: KeyRound, ownerOnly: true },
  { to: "/settings", label: "Paramètres", short: "Réglages", icon: Settings, ownerOnly: false },
];

export const appPageTitles: Record<string, [string, string]> = {
  "/": ["Vue d'ensemble", "Ta situation académique en un coup d'œil"],
  "/results": ["Résultats", "UE, évaluations et nouveautés dans un espace unique"],
  "/calendar": ["Agenda", "Tes cours et ton rythme de formation au même endroit"],
  "/ues/releve": ["Relevé académique", "Prépare un document personnel clair et partageable"],
  "/simulations/gpa": ["Simulations", "Projette ton GPA sans modifier tes données officielles"],
  "/simulations/notes": ["Simulations", "Teste tes prochaines notes et leur impact sur ta moyenne"],
  "/leaderboard": ["Classement", "Deux classements privés, calculés depuis PASS"],
  "/sharing": ["Partage", "Accès révocables liés à ton compte"],
  "/settings": ["Paramètres", "Compte, synchronisation et notifications"],
};

export const mobileNavDescriptions: Record<string, string> = {
  "/results": "Grades, crédits et détail des évaluations",
  "/calendar": "Cours personnels et calendrier de formation",
  "/sharing": "Tokens et accès partagés",
  "/settings": "Compte, synchronisation et notifications",
  "/parcours": "Cours, exercices guidés et progression privée",
};

export function isAppNavItemActive(to: string, pathname: string): boolean {
  if (to === "/results") return pathname.startsWith("/results");
  if (to === "/parcours") return pathname.startsWith("/parcours");
  if (to.startsWith("/simulations")) return pathname.startsWith("/simulations");
  return to === pathname;
}

export function visibleAppNavigation(session: Session, primaryOwner: boolean): AppNavItem[] {
  return appNavItems.filter((item) => {
    if (item.learningOnly) return learningEntryVisible(session);
    return (!item.ownerOnly || session.role === "owner") && (!item.primaryOwnerOnly || primaryOwner);
  });
}

export function appPageHeading(pathname: string, session: Session): [string, string] {
  if (pathname.startsWith("/results")) return appPageTitles["/results"]!;
  if (pathname.startsWith("/parcours")) {
    return ["Parcours", readerAudienceSubtitle(session.learning?.audience_label, session.learning?.level_label)];
  }
  return appPageTitles[pathname] ?? appPageTitles["/"]!;
}
