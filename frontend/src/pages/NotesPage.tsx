import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Filter, Pencil, Plus, Search, Trash2, TriangleAlert } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { formatDate, formatNumber, yearLabel } from "../lib/format";
import { queryKeys, useDashboard } from "../lib/queries";
import type { NoteItem, Role } from "../types";

interface EditorValues {
  ue_code: string;
  label: string;
  score: string;
  coefficient: string;
  is_resit: boolean;
}

function initialValues(note: NoteItem | null): EditorValues {
  return {
    ue_code: note?.ue_code ?? "",
    label: note?.label ?? "",
    score: note ? String(note.score) : "",
    coefficient: note ? String(note.coefficient) : "1",
    is_resit: note?.is_resit ?? false
  };
}

function NoteEditor({ note, onClose }: { note: NoteItem | null; onClose: () => void }) {
  const [values, setValues] = useState(() => initialValues(note));
  const [deleteArmed, setDeleteArmed] = useState(false);
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.account });
  const save = useMutation({
    mutationFn: () => api<NoteItem>(note ? `/api/v1/notes/${note.id}` : "/api/v1/notes", {
      method: note ? "PATCH" : "POST",
      body: JSON.stringify({ ...values, score: Number(values.score), coefficient: Number(values.coefficient) })
    }),
    onSuccess: () => { refresh(); showToast(note ? "Note mise à jour" : "Note ajoutée"); onClose(); },
    onError: (error) => showToast(error.message, "error")
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/notes/${note?.id}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); showToast("Note supprimée"); onClose(); },
    onError: (error) => showToast(error.message, "error")
  });
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate(); };

  return (
    <Modal open title={note ? "Modifier la note manuelle" : "Ajouter une note manuelle"} description="Les notes PASS restent officielles et en lecture seule." onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <div className="form-grid two-columns">
          <label>UE<input value={values.ue_code} onChange={(event) => setValues({ ...values, ue_code: event.target.value.toUpperCase() })} placeholder="SIT130" required /></label>
          <label>Coefficient<input type="number" min="0.01" max="100" step="0.01" value={values.coefficient} onChange={(event) => setValues({ ...values, coefficient: event.target.value })} required /></label>
        </div>
        <label>Évaluation<input value={values.label} onChange={(event) => setValues({ ...values, label: event.target.value })} placeholder="Examen final" required /></label>
        <label>Note sur 20<input className="score-input" type="number" min="0" max="20" step="0.01" value={values.score} onChange={(event) => setValues({ ...values, score: event.target.value })} required /></label>
        <label className="toggle-row"><span><strong>Rattrapage</strong><small>Une UE validée après RAT reçoit le grade E et un GPA de 2,5.</small></span><input type="checkbox" checked={values.is_resit} onChange={(event) => setValues({ ...values, is_resit: event.target.checked })} /><i /></label>
        {save.error && <div className="form-error">{save.error.message}</div>}
        <footer className="modal-actions split-actions">
          <div>{note && <button className={deleteArmed ? "danger-button armed" : "danger-button"} type="button" onClick={() => deleteArmed ? remove.mutate() : setDeleteArmed(true)} disabled={remove.isPending}><Trash2 size={16} /> {deleteArmed ? "Confirmer" : "Supprimer"}</button>}</div>
          <div><button className="secondary-button" type="button" onClick={onClose}>Annuler</button><button className="primary-button" type="submit" disabled={save.isPending}>{save.isPending ? <span className="spinner" /> : null} Enregistrer</button></div>
        </footer>
      </form>
    </Modal>
  );
}

export function NotesPage({ role }: { role: Role }) {
  const dashboard = useDashboard();
  const [search, setSearch] = useState("");
  const [year, setYear] = useState("all");
  const [semester, setSemester] = useState("all");
  const [source, setSource] = useState("all");
  const [editing, setEditing] = useState<NoteItem | null | undefined>(undefined);
  const yearByUe = useMemo(() => new Map(dashboard.data?.ues.map((ue) => [ue.code, ue.year]) ?? []), [dashboard.data?.ues]);
  const semesterByUe = useMemo(() => new Map(dashboard.data?.ues.map((ue) => [ue.code, ue.semester]) ?? []), [dashboard.data?.ues]);
  const notes = useMemo(() => (dashboard.data?.notes ?? []).filter((note) => {
    const query = search.trim().toLocaleLowerCase("fr");
    const matchesSearch = !query || `${note.ue_code} ${note.label}`.toLocaleLowerCase("fr").includes(query);
    const matchesYear = year === "all" || yearByUe.get(note.ue_code) === year;
    const matchesSemester = semester === "all" || semesterByUe.get(note.ue_code) === semester;
    const matchesSource = source === "all" || note.source === source;
    return matchesSearch && matchesYear && matchesSemester && matchesSource;
  }), [dashboard.data?.notes, search, semester, semesterByUe, source, year, yearByUe]);

  if (dashboard.isPending) return <div className="table-skeleton skeleton" />;
  if (dashboard.isError || !dashboard.data) return <div className="error-panel"><TriangleAlert size={22} /><span>{dashboard.error?.message}</span></div>;
  const years = [...new Set(dashboard.data.ues.map((ue) => ue.year).filter(Boolean))].sort();
  const semesters = dashboard.data.semesters;

  return (
    <div className="page-stack">
      <section className="toolbar-band">
        <div className="search-field"><Search size={18} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher une UE ou une évaluation" aria-label="Rechercher une note" /></div>
        <div className="filter-group"><Filter size={17} /><select value={year} onChange={(event) => setYear(event.target.value)} aria-label="Filtrer par année"><option value="all">Toutes les années</option>{years.map((item) => <option key={item} value={item}>{yearLabel(item)}</option>)}</select>{semesters.length > 0 && <select value={semester} onChange={(event) => setSemester(event.target.value)} aria-label="Filtrer par semestre"><option value="all">Tous les semestres</option>{semesters.map((item) => <option key={item.semester} value={item.semester}>{item.label}</option>)}</select>}<select value={source} onChange={(event) => setSource(event.target.value)} aria-label="Filtrer par source"><option value="all">Toutes les sources</option><option value="pass">PASS</option><option value="manual">Manuelles</option></select></div>
        {role !== "viewer" && <button className="primary-button" type="button" onClick={() => setEditing(null)}><Plus size={18} /> Ajouter</button>}
      </section>

      <section className="data-section">
        <header className="section-heading"><div><h2>Toutes les notes</h2><p>{notes.length} résultat{notes.length > 1 ? "s" : ""} affiché{notes.length > 1 ? "s" : ""}</p></div></header>
        {notes.length ? <div className="table-wrap"><table className="data-table notes-table"><thead><tr><th>UE</th><th>Évaluation</th><th>Note</th><th>Coeff.</th><th>Source</th><th>Détection</th>{role !== "viewer" && <th><span className="sr-only">Actions</span></th>}</tr></thead><tbody>{notes.map((note) => <tr key={note.id}><td><strong className="code-label">{note.ue_code}</strong></td><td><div className="cell-primary"><strong>{note.label}</strong><span>{note.is_resit ? "Rattrapage" : "Évaluation classique"}</span></div></td><td data-label="Note"><strong className={note.score >= 10 ? "score success" : "score danger"}>{formatNumber(note.score)}<small>/20</small></strong></td><td data-label="Coeff.">{formatNumber(note.coefficient)}</td><td data-label="Source"><span className={`source-badge ${note.source}`}>{note.source === "pass" ? "PASS" : "Manuelle"}</span></td><td data-label="Détection"><span className="date-cell">{formatDate(note.detected_at, false)}</span></td>{role !== "viewer" && <td>{note.editable ? <button className="icon-button" type="button" onClick={() => setEditing(note)} aria-label={`Modifier ${note.label}`} title="Modifier"><Pencil size={17} /></button> : <span className="official-lock" title="Note PASS officielle en lecture seule"><BadgeCheck size={16} /><span className="sr-only">Note PASS officielle en lecture seule</span></span>}</td>}</tr>)}</tbody></table></div> : <EmptyState title="Aucune note trouvée" detail="Modifie les filtres ou ajoute une note manuellement." />}
      </section>
      {editing !== undefined && <NoteEditor note={editing} onClose={() => setEditing(undefined)} />}
    </div>
  );
}
