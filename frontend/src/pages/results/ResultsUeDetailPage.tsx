import { ArrowLeft, BadgeCheck, BookOpenCheck, ChevronRight, SearchX, TriangleAlert } from "lucide-react";
import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { EmptyState } from "../../components/EmptyState";
import { GradeBadge } from "../../components/GradeBadge";
import { formatDate, formatNumber, yearLabel } from "../../lib/format";
import { useDashboard } from "../../lib/queries";
import { ResultsEvaluationItem } from "./ResultsEvaluationItem";
import { buildResultsIndex } from "./resultsSelectors";
import { sanitizeResultsSearch } from "./resultsState";
import { gradeSourceLabel, ResultsUeStatus } from "./ResultsUeCard";

export function ResultsUeDetailPage() {
  const dashboard = useDashboard();
  const { ueCode = "" } = useParams();
  const [searchParams] = useSearchParams();
  const index = useMemo(
    () => buildResultsIndex(dashboard.data?.ues ?? [], dashboard.data?.notes ?? []),
    [dashboard.data?.notes, dashboard.data?.ues],
  );
  const returnParams = sanitizeResultsSearch(searchParams, {
    years: new Set(index.years),
    semesters: new Set(index.semesters),
    ues: new Set(index.ueCodes),
  });
  const returnSearch = `?${returnParams.toString()}`;
  const backUrl = `/results${returnSearch}`;

  if (dashboard.isPending) {
    return <div className="results-detail-loading skeleton" aria-label="Chargement de l'UE" />;
  }
  if (dashboard.isError || !dashboard.data) {
    return (
      <section className="error-panel results-error" role="alert">
        <TriangleAlert size={22} aria-hidden="true" />
        <div>
          <h2>UE indisponible</h2>
          <p>{dashboard.error?.message ?? "Impossible de charger cette unité d'enseignement."}</p>
        </div>
      </section>
    );
  }

  const ue = dashboard.data.ues.find((item) => item.code.toLocaleLowerCase("fr") === ueCode.toLocaleLowerCase("fr"));
  if (!ue) {
    return (
      <div className="results-detail-not-found">
        <EmptyState
          icon={<SearchX size={23} />}
          title="UE introuvable"
          detail="Cette unité d'enseignement n'existe pas dans les résultats disponibles."
          action={
            <Link className="secondary-button" to={backUrl} replace>
              <ArrowLeft size={16} aria-hidden="true" /> Revenir aux résultats
            </Link>
          }
        />
      </div>
    );
  }

  const notes = index.notesByUe.get(ue.code) ?? [];
  const showSource = new Set(dashboard.data.notes.map((note) => note.source)).size > 1;
  const allocatedCredits = ue.credits_ects === null ? "Indisponibles" : `${formatNumber(ue.credits_ects)} ECTS`;
  const earnedCredits =
    ue.earned_credits_ects === null ? "Indisponibles" : `${formatNumber(ue.earned_credits_ects)} ECTS`;

  return (
    <div className="results-detail-page">
      <nav className="results-breadcrumbs" aria-label="Fil d'Ariane">
        <Link to={backUrl} viewTransition>
          Résultats
        </Link>
        <ChevronRight size={15} aria-hidden="true" />
        <span aria-current="page">{ue.code}</span>
      </nav>

      <section className="results-detail-hero">
        <div>
          <span className="section-kicker">
            {ue.semester ?? yearLabel(ue.year)} · {ue.code}
          </span>
          <h2>{ue.title || ue.code}</h2>
          {ue.official_code && <p>Code officiel {ue.official_code}</p>}
        </div>
        <ResultsUeStatus ue={ue} />
      </section>

      <section className="results-detail-summary" aria-label="Résumé académique de l'UE">
        <dl>
          <div>
            <dt>Moyenne PASS</dt>
            <dd>{formatNumber(ue.average, " /20")}</dd>
          </div>
          <div>
            <dt>Grade</dt>
            <dd>
              <GradeBadge grade={ue.grade} description={ue.grade_description} />
            </dd>
          </div>
          <div>
            <dt>GPA</dt>
            <dd>{formatNumber(ue.gpa, " /4")}</dd>
          </div>
          <div>
            <dt>ECTS obtenus</dt>
            <dd>{earnedCredits}</dd>
          </div>
          <div>
            <dt>ECTS alloués</dt>
            <dd>{allocatedCredits}</dd>
          </div>
          <div>
            <dt>Évaluations</dt>
            <dd>{notes.length}</dd>
          </div>
        </dl>
      </section>

      <section className="results-detail-section" aria-labelledby="results-detail-evaluations">
        <header>
          <div>
            <span className="section-kicker">Données PASS</span>
            <h3 id="results-detail-evaluations">Évaluations</h3>
          </div>
          <span>
            {notes.length} résultat{notes.length > 1 ? "s" : ""}
          </span>
        </header>
        {notes.length ? (
          <ul className="results-evaluation-list results-evaluation-list-main">
            {notes.map((note) => (
              <ResultsEvaluationItem
                key={note.id}
                note={note}
                ue={ue}
                returnSearch={returnSearch}
                showSource={showSource}
                showUeLink={false}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={<BookOpenCheck size={22} />}
            title="Aucune évaluation détaillée"
            detail="L'UE est connue, mais aucune évaluation PASS n'est disponible."
          />
        )}
      </section>

      <section className="results-detail-sources" aria-labelledby="results-detail-sources">
        <div>
          <BadgeCheck size={19} aria-hidden="true" />
          <div>
            <h3 id="results-detail-sources">Calculs et sources</h3>
            <p>
              {gradeSourceLabel(ue.grade_source)}. Moyenne, GPA, validation et ECTS sont fournis par le backend
              IMTégrale sans recalcul dans cette page.
            </p>
          </div>
        </div>
        <dl>
          <div>
            <dt>Métadonnées académiques</dt>
            <dd>{ue.metadata_source === "competences" ? "COMPETENCES" : "Données historiques"}</dd>
          </div>
          <div>
            <dt>Fraîcheur</dt>
            <dd>
              {ue.metadata_refreshed_at
                ? `Actualisée le ${formatDate(ue.metadata_refreshed_at, false)}`
                : "Date indisponible"}
            </dd>
          </div>
        </dl>
      </section>

      <Link className="secondary-button results-back-link" to={backUrl} viewTransition>
        <ArrowLeft size={16} aria-hidden="true" /> Revenir aux résultats
      </Link>
    </div>
  );
}
