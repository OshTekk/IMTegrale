import { Database, ExternalLink, Info } from "lucide-react";
import { BRAND } from "../brand";

export function SourceNotice({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <div className="source-notice compact" aria-label="Origine des données et indépendance du service">
        <span>
          <Database size={13} /> Données importées depuis {BRAND.sourceName}
        </span>
        <span>
          <Info size={13} /> Service étudiant indépendant
        </span>
      </div>
    );
  }

  return (
    <aside className="source-notice" aria-label="Origine des données et indépendance du service">
      <span className="source-notice-icon">
        <Database size={19} />
      </span>
      <div>
        <strong>Données importées depuis votre compte {BRAND.sourceName}</strong>
        <span>Portail de scolarité d’{BRAND.institution}</span>
        <small>
          <Info size={12} /> {BRAND.independenceNotice}
        </small>
      </div>
      <a
        href={BRAND.sourceUrl}
        target="_blank"
        rel="noreferrer"
        aria-label={`Ouvrir ${BRAND.sourceName}`}
        title={`Ouvrir ${BRAND.sourceName}`}
      >
        <ExternalLink size={16} />
      </a>
    </aside>
  );
}
