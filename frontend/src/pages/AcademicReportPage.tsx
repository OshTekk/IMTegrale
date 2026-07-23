import {
  ArrowLeft,
  BadgeCheck,
  BookOpenCheck,
  CalendarRange,
  Download,
  ExternalLink,
  FileCheck2,
  FileText,
  Info,
  LockKeyhole,
  TriangleAlert,
} from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BRAND } from "../brand";
import { GitHubMark } from "../components/GitHubMark";
import { BrandMark } from "../components/Logo";
import { useToast } from "../components/Toast";
import { downloadFile } from "../lib/api";
import { formatDate, formatNumber } from "../lib/format";
import { useDashboard, useSettings } from "../lib/queries";
import type { AcademicSemester, UeItem } from "../types";

type ReportScope = "all" | AcademicSemester;
const REPORT_PREVIEW_WIDTH = 720;
const REPORT_PREVIEW_HEIGHT = 1018;

function weightedValue(ues: UeItem[], key: "average" | "gpa"): { value: number | null; credits: number } {
  let total = 0;
  let credits = 0;
  for (const ue of ues) {
    const value = ue[key];
    if (value === null || ue.credits_ects === null || ue.credits_ects <= 0) continue;
    total += value * ue.credits_ects;
    credits += ue.credits_ects;
  }
  return { value: credits ? Math.round((total / credits) * 100) / 100 : null, credits };
}

function validatedCredits(ues: UeItem[]): number {
  return ues.reduce((total, ue) => {
    if (!ue.validated) return total;
    return total + (ue.earned_credits_ects ?? ue.credits_ects ?? 0);
  }, 0);
}

function attemptedCredits(ues: UeItem[]): number {
  return ues.reduce((total, ue) => total + (ue.credits_ects ?? 0), 0);
}

function profileLabel(program: string, promotionYear: number | null, campus: string): string {
  return (
    [
      program === "unknown" ? null : program.toUpperCase(),
      promotionYear ? `promotion ${promotionYear}` : null,
      campus === "unknown" ? null : campus.charAt(0).toUpperCase() + campus.slice(1),
    ]
      .filter(Boolean)
      .join(" · ") || "Profil académique non disponible"
  );
}

function ReportMetric({
  label,
  value,
  suffix,
  detail,
}: {
  label: string;
  value: string;
  suffix: string;
  detail: string;
}) {
  return (
    <div>
      <span>{label}</span>
      <strong>
        {value} <small>{suffix}</small>
      </strong>
      <p>{detail}</p>
    </div>
  );
}

export function AcademicReportPage() {
  const dashboard = useDashboard();
  const settings = useSettings();
  const { showToast } = useToast();
  const [scope, setScope] = useState<ReportScope>("all");
  const [includeAssessments, setIncludeAssessments] = useState(true);
  const [identityRequested, setIdentityRequested] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [previewScale, setPreviewScale] = useState(1);
  const previewStageRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const stage = previewStageRef.current;
    if (!stage) return;
    const updateScale = () => {
      const style = window.getComputedStyle(stage);
      const horizontalPadding = Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight);
      const availableWidth = Math.max(0, stage.clientWidth - horizontalPadding);
      setPreviewScale(Math.min(1, Math.max(0.35, availableWidth / REPORT_PREVIEW_WIDTH)));
    };
    const observer = new ResizeObserver(updateScale);
    observer.observe(stage);
    updateScale();
    return () => observer.disconnect();
  }, [dashboard.isPending, settings.isPending]);

  const availableSemesters = useMemo(
    () => dashboard.data?.semesters.map((item) => item.semester) ?? [],
    [dashboard.data?.semesters],
  );
  const selectedUes = useMemo(
    () => (dashboard.data?.ues ?? []).filter((ue) => scope === "all" || ue.semester === scope),
    [dashboard.data?.ues, scope],
  );
  const selectedCodes = useMemo(() => new Set(selectedUes.map((ue) => ue.code)), [selectedUes]);
  const selectedNotes = useMemo(
    () => (dashboard.data?.notes ?? []).filter((note) => selectedCodes.has(note.ue_code)),
    [dashboard.data?.notes, selectedCodes],
  );
  const average = useMemo(() => weightedValue(selectedUes, "average"), [selectedUes]);
  const gpa = useMemo(() => weightedValue(selectedUes, "gpa"), [selectedUes]);
  const officialName = settings.data?.account.official_name ?? null;
  const includeIdentity = identityRequested && Boolean(officialName);
  const semesterRows = useMemo(
    () =>
      availableSemesters
        .filter((semester) => scope === "all" || semester === scope)
        .map((semester) => {
          const ues = selectedUes.filter((ue) => ue.semester === semester);
          return {
            semester,
            average: weightedValue(ues, "average"),
            gpa: weightedValue(ues, "gpa"),
            validated: validatedCredits(ues),
            attempted: attemptedCredits(ues),
            count: ues.length,
          };
        }),
    [availableSemesters, scope, selectedUes],
  );

  if (dashboard.isPending || settings.isPending) {
    return <div className="report-page-skeleton skeleton" aria-label="Chargement du relevé" />;
  }
  if (dashboard.isError || settings.isError || !dashboard.data || !settings.data) {
    return (
      <div className="error-panel">
        <TriangleAlert size={22} />
        {dashboard.error?.message ?? settings.error?.message ?? "Impossible de préparer le relevé"}
      </div>
    );
  }

  const profile = settings.data.account;
  const competencesDate = selectedUes
    .map((ue) => ue.metadata_refreshed_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);
  const scopeLabel = scope === "all" ? "Tous les semestres disponibles" : `Semestre ${scope}`;
  const missingEcts = selectedUes.filter((ue) => ue.credits_ects === null).length;

  const download = async () => {
    setDownloading(true);
    try {
      const query = new URLSearchParams({
        semester: scope,
        include_assessments: String(includeAssessments),
        include_identity: String(includeIdentity),
      });
      const file = await downloadFile(`/api/v1/academic-reports/personal.pdf?${query.toString()}`);
      const url = URL.createObjectURL(file.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = file.filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
      showToast("Relevé académique téléchargé");
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Impossible de générer le relevé", "error");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="academic-report-page">
      <section className="report-intro-band">
        <Link
          className="icon-button"
          to="/results?view=ues"
          viewTransition
          aria-label="Revenir aux résultats"
          title="Revenir aux résultats"
        >
          <ArrowLeft size={18} />
        </Link>
        <div>
          <span className="section-kicker">Document partageable</span>
          <h2>Relevé académique personnel</h2>
          <p>Une synthèse A4 à titre informatif, construite depuis tes données PASS et COMPETENCES.</p>
        </div>
        <span className="report-private-label">
          <LockKeyhole size={16} /> Généré en mémoire
        </span>
      </section>

      <div className="academic-report-workspace">
        <aside className="report-options-panel" aria-label="Préparer le relevé">
          <header>
            <span>
              <FileText size={20} />
            </span>
            <div>
              <h2>Préparer le document</h2>
              <p>Le PDF n'est jamais conservé sur le serveur.</p>
            </div>
          </header>

          <div className="report-option-group">
            <label htmlFor="report-scope">
              <span>
                <CalendarRange size={16} /> Périmètre académique
              </span>
            </label>
            <select id="report-scope" value={scope} onChange={(event) => setScope(event.target.value as ReportScope)}>
              <option value="all">Tous les semestres</option>
              {availableSemesters.map((semester) => (
                <option key={semester} value={semester}>
                  {semester}
                </option>
              ))}
            </select>
            <small>
              {selectedUes.length} UE et {selectedNotes.length} évaluations dans ce périmètre.
            </small>
          </div>

          <label className="report-toggle-row">
            <span>
              <strong>Identité PASS</strong>
              <small>Inclure ton prénom et ton nom synchronisés.</small>
            </span>
            <span className="switch">
              <input
                type="checkbox"
                role="switch"
                checked={includeIdentity}
                disabled={!officialName}
                onChange={(event) => setIdentityRequested(event.target.checked)}
                aria-label="Inclure l'identité PASS"
              />
              <i />
            </span>
          </label>
          {!officialName && (
            <p className="report-option-warning">
              <Info size={15} /> L'identité PASS n'est pas encore disponible. Le relevé sera anonymisé.
            </p>
          )}

          <label className="report-toggle-row">
            <span>
              <strong>Annexe des évaluations</strong>
              <small>Ajouter les notes PASS et leurs coefficients.</small>
            </span>
            <span className="switch">
              <input
                type="checkbox"
                role="switch"
                checked={includeAssessments}
                onChange={(event) => setIncludeAssessments(event.target.checked)}
                aria-label="Inclure l'annexe des évaluations"
              />
              <i />
            </span>
          </label>

          {missingEcts > 0 && (
            <p className="report-option-warning">
              <TriangleAlert size={15} /> {missingEcts} UE sans ECTS sera signalée comme indisponible.
            </p>
          )}

          <div className="report-export-summary">
            <span>
              <FileCheck2 size={17} />
            </span>
            <div>
              <strong>{includeAssessments ? "Relevé complet" : "Relevé synthétique"}</strong>
              <small>
                {scopeLabel} · {includeIdentity ? "identité incluse" : "version anonymisée"}
              </small>
            </div>
          </div>

          <button
            className="primary-button report-download-button"
            type="button"
            onClick={download}
            disabled={downloading || selectedUes.length === 0}
          >
            {downloading ? <span className="spinner" /> : <Download size={18} />}
            {downloading ? "Génération…" : "Télécharger le PDF"}
          </button>
        </aside>

        <section className="report-preview-panel" aria-label="Aperçu du relevé">
          <header>
            <div>
              <span className="section-kicker">Aperçu</span>
              <h2>Première page</h2>
            </div>
            <span>Format A4 · PDF</span>
          </header>
          <div className="report-preview-stage" ref={previewStageRef}>
            <div
              className="report-sheet-frame"
              style={{ width: REPORT_PREVIEW_WIDTH * previewScale, height: REPORT_PREVIEW_HEIGHT * previewScale }}
            >
              <article className="report-sheet" style={{ transform: `scale(${previewScale})` }}>
                <header className="report-sheet-header">
                  <div className="report-sheet-brand">
                    <BrandMark size={32} />
                    <span>
                      <strong>IMTégrale</strong>
                      <small>Suivi académique étudiant indépendant</small>
                    </span>
                  </div>
                  <div>
                    <strong>
                      RELEVÉ ACADÉMIQUE
                      <br />
                      PERSONNEL
                    </strong>
                    <span>À TITRE INFORMATIF</span>
                  </div>
                </header>

                <section className="report-sheet-identity">
                  <div>
                    <span>{includeIdentity ? "IDENTITÉ SYNCHRONISÉE DEPUIS PASS" : "VERSION ANONYMISÉE"}</span>
                    <strong>{includeIdentity ? officialName : "Identité masquée"}</strong>
                    <small>{profileLabel(profile.program, profile.promotion_year, profile.campus)}</small>
                  </div>
                  <div>
                    <span>PÉRIMÈTRE DU RELEVÉ</span>
                    <strong>{scopeLabel}</strong>
                    <small>Généré le {formatDate(new Date().toISOString())}</small>
                  </div>
                </section>

                <p className="report-sheet-source-notice">
                  <BadgeCheck size={13} />
                  <span>
                    <strong>Données institutionnelles synchronisées.</strong> Les valeurs calculées par IMTégrale sont
                    identifiées dans le relevé.
                  </span>
                </p>

                <section className="report-sheet-section">
                  <h3>Synthèse</h3>
                  <div className="report-sheet-metrics">
                    <ReportMetric
                      label="MOYENNE GÉNÉRALE"
                      value={formatNumber(average.value)}
                      suffix="/ 20"
                      detail={`${formatNumber(average.credits)} ECTS pondérés`}
                    />
                    <ReportMetric
                      label="GPA GLOBAL"
                      value={formatNumber(gpa.value)}
                      suffix="/ 4"
                      detail={`${formatNumber(gpa.credits)} ECTS pondérés`}
                    />
                    <ReportMetric
                      label="CRÉDITS ECTS"
                      value={formatNumber(validatedCredits(selectedUes))}
                      suffix="obtenus"
                      detail={`sur ${formatNumber(attemptedCredits(selectedUes))} alloués`}
                    />
                    <ReportMetric
                      label="UNITÉS D'ENSEIGNEMENT"
                      value={formatNumber(selectedUes.length)}
                      suffix="UE"
                      detail={`${formatNumber(selectedNotes.length)} évaluations PASS`}
                    />
                  </div>
                </section>

                <section className="report-sheet-section report-semester-preview">
                  <h3>Résultats par semestre</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Semestre</th>
                        <th>Moyenne</th>
                        <th>GPA</th>
                        <th>ECTS obtenus / alloués</th>
                        <th>UE</th>
                      </tr>
                    </thead>
                    <tbody>
                      {semesterRows.map((item) => (
                        <tr key={item.semester}>
                          <td>
                            <strong>{item.semester}</strong>
                          </td>
                          <td>{formatNumber(item.average.value)} / 20</td>
                          <td>{formatNumber(item.gpa.value)} / 4</td>
                          <td>
                            {formatNumber(item.validated)} / {formatNumber(item.attempted)}
                          </td>
                          <td>{item.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>

                <section className="report-sheet-provenance">
                  <div>
                    <span>PROVENANCE ET TRANSPARENCE</span>
                    <p>
                      <strong>PASS</strong> Évaluations synchronisées le{" "}
                      {formatDate(dashboard.data.account.last_sync_at)}
                    </p>
                    <p>
                      <strong>COMPETENCES</strong> Intitulés, semestres, grades et ECTS synchronisés le{" "}
                      {formatDate(competencesDate)}
                    </p>
                    <p>
                      <strong>IMTégrale</strong> Moyennes, GPA, regroupements et mise en page.
                    </p>
                    <a href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer">
                      <GitHubMark size={13} /> Consulter le code source <ExternalLink size={11} />
                    </a>
                  </div>
                  <GitHubMark size={32} />
                </section>

                <p className="report-sheet-legal">
                  <strong>Document personnel non officiel.</strong> Ce relevé est fourni à titre informatif. Il n'est ni
                  édité, ni certifié, ni validé par IMT Atlantique et ne remplace pas un relevé officiel.
                </p>
                <footer>
                  <span>Document personnel non officiel · Généré par IMTégrale</span>
                  <span>Page 1</span>
                </footer>
              </article>
            </div>
          </div>
          <div className="report-preview-followup">
            <BookOpenCheck size={17} />
            <span>
              <strong>Le PDF complet continue avec {selectedUes.length} UE</strong>
              <small>
                {includeAssessments
                  ? `puis l'annexe de ${selectedNotes.length} évaluations PASS`
                  : "sans annexe des évaluations"}
                .
              </small>
            </span>
          </div>
        </section>
      </div>

      <section className="report-trust-band">
        <span>
          <Info size={19} />
        </span>
        <div>
          <h2>Un fonctionnement vérifiable</h2>
          <p>
            Le dépôt public permet d'examiner l'import et les calculs. Il apporte de la transparence ; seul IMT
            Atlantique peut émettre un relevé officiel.
          </p>
        </div>
        <a className="secondary-button" href={BRAND.sourceCodeUrl} target="_blank" rel="noreferrer">
          <GitHubMark size={17} /> Voir le dépôt <ExternalLink size={15} />
        </a>
      </section>
    </div>
  );
}
