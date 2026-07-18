import { Fragment, useMemo, useState } from "react";
import { BadgeCheck, BookOpenCheck, CheckCircle2, ChevronDown, CircleDashed, TriangleAlert } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { GradeBadge } from "../components/GradeBadge";
import { formatDate, formatNumber, yearLabel } from "../lib/format";
import { useDashboard } from "../lib/queries";

export function UesPage() {
  const dashboard = useDashboard();
  const [year, setYear] = useState("all");
  const [semester, setSemester] = useState("all");
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const ues = useMemo(() => (dashboard.data?.ues ?? []).filter((ue) => (
    (year === "all" || ue.year === year)
    && (semester === "all" || ue.semester === semester)
  )), [dashboard.data?.ues, semester, year]);
  const notesByUe = useMemo(() => {
    const grouped = new Map<string, NonNullable<typeof dashboard.data>["notes"]>();
    for (const note of dashboard.data?.notes ?? []) {
      const notes = grouped.get(note.ue_code) ?? [];
      notes.push(note);
      grouped.set(note.ue_code, notes);
    }
    return grouped;
  }, [dashboard.data]);

  if (dashboard.isPending) return <div className="table-skeleton skeleton" />;
  if (dashboard.isError || !dashboard.data) return <div className="error-panel"><TriangleAlert size={22} />{dashboard.error?.message}</div>;
  const years = dashboard.data.years;
  const semesters = dashboard.data.semesters;

  return (
    <div className="page-stack">
      <section className="ue-summary-band">
        <div className="year-tabs" role="tablist" aria-label="Année académique"><button type="button" role="tab" aria-selected={year === "all"} className={year === "all" ? "active" : ""} onClick={() => setYear("all")}>Toutes <span>{dashboard.data.ues.length}</span></button>{years.map((item) => <button key={item.year} type="button" role="tab" aria-selected={year === item.year} className={year === item.year ? "active" : ""} onClick={() => setYear(item.year)}>{item.label} <span>{item.ue_count}</span></button>)}</div>
        <div className="weighting-note"><BookOpenCheck size={18} /><span><strong>Pondération ECTS active</strong>La moyenne et le GPA généraux utilisent les crédits de chaque UE.</span></div>
      </section>

      {semesters.length > 0 && <section className="semester-filter-band" aria-label="Filtrer par semestre"><div className="segmented semester-tabs" role="tablist" aria-label="Semestre"><button type="button" role="tab" aria-selected={semester === "all"} className={semester === "all" ? "active" : ""} onClick={() => setSemester("all")}>Tous les semestres</button>{semesters.map((item) => <button key={item.semester} type="button" role="tab" aria-selected={semester === item.semester} className={semester === item.semester ? "active" : ""} onClick={() => setSemester(item.semester)}>{item.semester}</button>)}</div><p><BadgeCheck size={16} /> Semestres, intitulés, grades et ECTS importés depuis COMPETENCES.</p></section>}

      <section className="grade-scale-band" aria-label="Échelle des grades">{dashboard.data.grade_scale.map((item) => <div key={item.grade}><GradeBadge grade={item.grade} /><span>{item.description}</span><strong>{formatNumber(item.gpa)}</strong></div>)}</section>

      <section className="data-section">
        <header className="section-heading"><div><h2>Unités d'enseignement</h2><p>{ues.length} UE · {ues.filter((ue) => ue.validated).length} validées</p></div><span className="official-data-label"><BadgeCheck size={16} /> PASS + COMPETENCES</span></header>
        {ues.length ? <div className="table-wrap"><table className="data-table ue-table"><thead><tr><th>Unité d'enseignement</th><th>Semestre</th><th>Moyenne PASS</th><th>Grade</th><th>GPA</th><th>ECTS</th><th>État</th><th><span className="sr-only">Détails</span></th></tr></thead><tbody>{ues.map((ue) => {
          const notes = notesByUe.get(ue.code) ?? [];
          const detailsId = `ue-notes-${ue.code.replace(/[^A-Za-z0-9_-]/g, "-")}`;
          const expanded = expandedCode === ue.code;
          return <Fragment key={ue.code}>
            <tr className={ue.credits_ects === null ? "needs-data" : ""}>
              <td><div className="cell-primary"><strong className="code-label">{ue.code}</strong><span>{ue.title || `${ue.note_count} note${ue.note_count > 1 ? "s" : ""}`}</span>{ue.official_code && <small>{ue.official_code}</small>}</div></td>
              <td data-label="Semestre">{ue.semester ?? yearLabel(ue.year)}</td>
              <td data-label="Moy. PASS"><strong>{formatNumber(ue.average, " /20")}</strong></td>
              <td data-label="Grade"><div className="grade-source-cell"><GradeBadge grade={ue.grade} description={ue.grade_description} /><small>{ue.grade_source === "competences" ? "COMPETENCES" : ue.grade_source === "pass_calculated" ? "Calcul PASS" : "Indisponible"}</small></div></td>
              <td data-label="GPA"><strong>{formatNumber(ue.gpa, " /4")}</strong></td>
              <td data-label="ECTS">{ue.credits_ects === null ? <span className="missing-value"><TriangleAlert size={15} /> Indisponible</span> : <span className="ects-official-value" title={ue.earned_credits_ects === null ? "ECTS alloués" : "ECTS obtenus / alloués"}><strong>{ue.earned_credits_ects === null ? formatNumber(ue.credits_ects) : `${formatNumber(ue.earned_credits_ects)} / ${formatNumber(ue.credits_ects)}`}</strong>{ue.metadata_source === "competences" && <BadgeCheck size={15} aria-label="Source officielle IMT" />}</span>}</td>
              <td data-label="État">{ue.grade === null && ue.average === null ? <span className="status-pill neutral"><CircleDashed size={14} /> En attente</span> : ue.used_resit ? <span className="status-pill warning">Rattrapage</span> : ue.validated ? <span className="status-pill success"><CheckCircle2 size={14} /> Validée</span> : <span className="status-pill danger">Non validée</span>}</td>
              <td><button className={`icon-button ue-expand-button ${expanded ? "is-open" : ""}`} type="button" onClick={() => setExpandedCode(expanded ? null : ue.code)} aria-expanded={expanded} aria-controls={detailsId} aria-label={`${expanded ? "Masquer" : "Voir"} les notes de ${ue.code}`} title={expanded ? "Masquer les notes" : "Voir les notes"}><ChevronDown size={18} /></button></td>
            </tr>
            {expanded && <tr className="ue-notes-row"><td colSpan={8}><div className="ue-notes-panel" id={detailsId}><header><div><span className="section-kicker">Évaluations PASS</span><strong>{ue.title || ue.code}</strong></div><span>{notes.length} note{notes.length > 1 ? "s" : ""}</span></header>{notes.length ? <div className="ue-note-list">{notes.map((note) => <div className="ue-note-item" key={note.id}><div><strong>{note.label}</strong><span>{note.is_resit ? "Rattrapage" : "Évaluation classique"} · détectée le {formatDate(note.detected_at, false)}</span></div><dl><div><dt>Note</dt><dd className={note.score >= 10 ? "success" : "danger"}>{formatNumber(note.score)} /20</dd></div><div><dt>Coefficient</dt><dd>{formatNumber(note.coefficient)}</dd></div></dl></div>)}</div> : <p className="ue-notes-empty">Aucune évaluation PASS détaillée n'est disponible pour cette UE.</p>}</div></td></tr>}
          </Fragment>;
        })}</tbody></table></div> : <EmptyState title="Aucune UE" detail="Aucune UE ne correspond aux filtres sélectionnés." />}
      </section>
    </div>
  );
}
