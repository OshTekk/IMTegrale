import { BadgeCheck, Filter, Search, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { formatDate, formatNumber, yearLabel } from "../lib/format";
import { useDashboard } from "../lib/queries";

export function NotesPage() {
  const dashboard = useDashboard();
  const [search, setSearch] = useState("");
  const [year, setYear] = useState("all");
  const [semester, setSemester] = useState("all");
  const yearByUe = useMemo(() => new Map(dashboard.data?.ues.map((ue) => [ue.code, ue.year]) ?? []), [dashboard.data?.ues]);
  const semesterByUe = useMemo(() => new Map(dashboard.data?.ues.map((ue) => [ue.code, ue.semester]) ?? []), [dashboard.data?.ues]);
  const notes = useMemo(() => (dashboard.data?.notes ?? []).filter((note) => {
    const query = search.trim().toLocaleLowerCase("fr");
    const matchesSearch = !query || `${note.ue_code} ${note.label}`.toLocaleLowerCase("fr").includes(query);
    const matchesYear = year === "all" || yearByUe.get(note.ue_code) === year;
    const matchesSemester = semester === "all" || semesterByUe.get(note.ue_code) === semester;
    return matchesSearch && matchesYear && matchesSemester;
  }), [dashboard.data?.notes, search, semester, semesterByUe, year, yearByUe]);

  if (dashboard.isPending) return <div className="table-skeleton skeleton" />;
  if (dashboard.isError || !dashboard.data) return <div className="error-panel"><TriangleAlert size={22} /><span>{dashboard.error?.message}</span></div>;
  const years = [...new Set(dashboard.data.ues.map((ue) => ue.year).filter(Boolean))].sort();
  const semesters = dashboard.data.semesters;

  return (
    <div className="page-stack">
      <section className="toolbar-band">
        <div className="search-field"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher une UE ou une évaluation" aria-label="Rechercher une note" /></div>
        <div className="filter-group"><Filter size={17} /><select value={year} onChange={(event) => setYear(event.target.value)} aria-label="Filtrer par année"><option value="all">Toutes les années</option>{years.map((item) => <option key={item} value={item}>{yearLabel(item)}</option>)}</select>{semesters.length > 0 && <select value={semester} onChange={(event) => setSemester(event.target.value)} aria-label="Filtrer par semestre"><option value="all">Tous les semestres</option>{semesters.map((item) => <option key={item.semester} value={item.semester}>{item.label}</option>)}</select>}</div>
      </section>

      <section className="data-section">
        <header className="section-heading"><div><h2>Toutes les notes</h2><p>{notes.length} résultat{notes.length > 1 ? "s" : ""} officiel{notes.length > 1 ? "s" : ""}</p></div><span className="official-data-label"><BadgeCheck size={16} /> Source PASS</span></header>
        {notes.length ? <div className="table-wrap"><table className="data-table notes-table"><thead><tr><th>UE</th><th>Évaluation</th><th>Note</th><th>Coeff.</th><th>Source</th><th>Détection</th></tr></thead><tbody>{notes.map((note) => <tr key={note.id}><td><strong className="code-label">{note.ue_code}</strong></td><td><div className="cell-primary"><strong>{note.label}</strong><span>{note.is_resit ? "Rattrapage" : "Évaluation classique"}</span></div></td><td data-label="Note"><strong className={note.score >= 10 ? "score success" : "score danger"}>{formatNumber(note.score)}<small>/20</small></strong></td><td data-label="Coeff.">{formatNumber(note.coefficient)}</td><td data-label="Source"><span className="source-badge pass">PASS</span></td><td data-label="Détection"><span className="date-cell">{formatDate(note.detected_at, false)}</span></td></tr>)}</tbody></table></div> : <EmptyState title="Aucune note trouvée" detail="Aucune note PASS ne correspond aux filtres sélectionnés." />}
      </section>
    </div>
  );
}
