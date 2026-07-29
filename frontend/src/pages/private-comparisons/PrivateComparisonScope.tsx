import { Check, ShieldCheck, X } from "lucide-react";

export function PrivateComparisonScope({ compact = false }: { compact?: boolean }) {
  return (
    <section
      className={`private-comparison-scope${compact ? " is-compact" : ""}`}
      aria-labelledby="comparison-scope-title"
    >
      <header>
        <ShieldCheck size={21} aria-hidden="true" />
        <div>
          <h2 id="comparison-scope-title">Un partage volontaire et limité</h2>
          <p>Les deux étudiants donnent leur accord et peuvent mettre fin à la comparaison.</p>
        </div>
      </header>
      <div className="private-comparison-scope-columns">
        <div>
          <h3>Ce qui est partagé</h3>
          <ul>
            {[
              "Identité officielle",
              "Moyenne générale, GPA et ECTS validés",
              "Répartition des grades",
              "UE communes",
              "Fraîcheur des données",
            ].map((item) => (
              <li key={item}>
                <Check size={16} aria-hidden="true" /> {item}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Ce qui ne l’est jamais dans cette version</h3>
          <ul>
            {[
              "Détail des évaluations et coefficients",
              "Simulations, agenda et Parcours",
              "Rang dans le classement",
              "Données d’un troisième étudiant",
            ].map((item) => (
              <li key={item}>
                <X size={16} aria-hidden="true" /> {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
