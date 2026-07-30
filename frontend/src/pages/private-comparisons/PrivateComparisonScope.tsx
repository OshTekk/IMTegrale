import { Check, ShieldCheck, X } from "lucide-react";
import type { PrivateComparisonConsentManifestResponse } from "../../generated/api/types.gen";

export function PrivateComparisonScope({
  manifest,
  compact = false,
}: {
  manifest: PrivateComparisonConsentManifestResponse;
  compact?: boolean;
}) {
  return (
    <section
      className={`private-comparison-scope${compact ? " is-compact" : ""}`}
      aria-label="Périmètre de la comparaison privée"
    >
      <header>
        <ShieldCheck size={21} aria-hidden="true" />
        <div>
          <h2>Un partage volontaire et limité</h2>
          <p>Les deux étudiants donnent leur accord et peuvent mettre fin à la comparaison.</p>
        </div>
      </header>
      <div className="private-comparison-scope-columns">
        <div>
          <h3>Ce qui est partagé</h3>
          {manifest.included_sections.map((section) => (
            <section key={section.key}>
              <h4>{section.title}</h4>
              <ul>
                {section.fields.map((field) => (
                  <li key={field.response_path}>
                    <Check size={16} aria-hidden="true" /> {field.label}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div>
          <h3>Ce qui ne l’est jamais dans cette version</h3>
          <ul>
            {manifest.excluded_sections.map((section) => (
              <li key={section.key}>
                <X size={16} aria-hidden="true" /> {section.label}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Durée, révocation et copies</h3>
          <ul>
            {Object.entries(manifest.duration_and_revocation).map(([key, label]) => (
              <li key={key}>{label}</li>
            ))}
            <li>{manifest.copy_risk}</li>
          </ul>
        </div>
      </div>
    </section>
  );
}
