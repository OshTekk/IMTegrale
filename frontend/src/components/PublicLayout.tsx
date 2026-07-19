import { ArrowLeft, ExternalLink, HelpCircle, Route as RouteIcon } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { BRAND } from "../brand";
import { GitHubMark } from "./GitHubMark";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function PublicLayout() {
  return (
    <div className="public-shell">
      <header className="public-header">
        <Logo />
        <nav aria-label="Pages publiques">
          <NavLink to="/confiance">
            <HelpCircle size={16} /> Confiance
          </NavLink>
          <NavLink to="/demo">
            <RouteIcon size={16} /> Démo
          </NavLink>
          <a href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer">
            <GitHubMark size={16} /> GitHub
          </a>
        </nav>
        <div className="public-header-actions">
          <ThemeToggle />
          <Link className="secondary-button" to="/">
            <ArrowLeft size={16} /> Connexion
          </Link>
        </div>
      </header>
      <main className="public-content">
        <Outlet />
      </main>
      <footer className="public-footer">
        <div>
          <strong>{BRAND.name}</strong>
          <span>{BRAND.independenceNotice}</span>
        </div>
        <p className="public-footer-links">
          <a href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer">
            <GitHubMark size={13} /> Code source
          </a>
          <span>
            Avec un clin d'œil à{" "}
            <a href={BRAND.paffUrl} target="_blank" rel="noreferrer">
              PAFF de Lucien Hervé <ExternalLink size={13} />
            </a>
            , projet étudiant antérieur.
          </span>
        </p>
      </footer>
    </div>
  );
}
