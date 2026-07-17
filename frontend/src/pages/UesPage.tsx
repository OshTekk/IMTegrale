import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, CheckCircle2, CircleDashed, Pencil, TriangleAlert } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { GradeBadge } from "../components/GradeBadge";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { api } from "../lib/api";
import { formatNumber, yearLabel } from "../lib/format";
import { queryKeys, useDashboard } from "../lib/queries";
import type { Role, UeItem } from "../types";

function UeEditor({ ue, onClose }: { ue: UeItem; onClose: () => void }) {
  const [credits, setCredits] = useState(ue.credits_ects === null ? "" : String(ue.credits_ects));
  const [title, setTitle] = useState(ue.title);
  const [year, setYear] = useState(ue.year);
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const save = useMutation({
    mutationFn: () => api(`/api/v1/ues/${encodeURIComponent(ue.code)}`, {
      method: "PATCH",
      body: JSON.stringify({ title, year, credits_ects: credits === "" ? null : Number(credits) })
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.account });
      showToast(`${ue.code} mise à jour`);
      onClose();
    },
    onError: (error) => showToast(error.message, "error")
  });
  const submit = (event: FormEvent) => { event.preventDefault(); save.mutate(); };
  return (
    <Modal open title={`Configurer ${ue.code}`} description="Le grade et le GPA restent calculés automatiquement à partir des notes." onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <label>Intitulé de l'UE<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Nom facultatif" /></label>
        <div className="form-grid two-columns">
          <label>Année<select value={year} onChange={(event) => setYear(event.target.value)}><option value="">Non classée</option><option value="1">1re année</option><option value="2">2e année</option><option value="3">3e année</option></select></label>
          <label>Crédits ECTS<input type="number" min="0" max="60" step="0.5" value={credits} onChange={(event) => setCredits(event.target.value)} placeholder="6" /></label>
        </div>
        <div className="calculated-preview"><div><span>Moyenne calculée</span><strong>{formatNumber(ue.average, " /20")}</strong></div><div><span>Grade</span><GradeBadge grade={ue.grade} description={ue.grade_description} /></div><div><span>GPA</span><strong>{formatNumber(ue.gpa, " /4")}</strong></div></div>
        <footer className="modal-actions"><button className="secondary-button" type="button" onClick={onClose}>Annuler</button><button className="primary-button" type="submit" disabled={save.isPending}>{save.isPending && <span className="spinner" />} Enregistrer</button></footer>
      </form>
    </Modal>
  );
}

export function UesPage({ role }: { role: Role }) {
  const dashboard = useDashboard();
  const [year, setYear] = useState("all");
  const [editing, setEditing] = useState<UeItem | null>(null);
  const ues = useMemo(() => (dashboard.data?.ues ?? []).filter((ue) => year === "all" || ue.year === year), [dashboard.data?.ues, year]);
  if (dashboard.isPending) return <div className="table-skeleton skeleton" />;
  if (dashboard.isError || !dashboard.data) return <div className="error-panel"><TriangleAlert size={22} />{dashboard.error?.message}</div>;
  const years = dashboard.data.years;

  return (
    <div className="page-stack">
      <section className="ue-summary-band">
        <div className="year-tabs" role="tablist" aria-label="Année académique"><button type="button" role="tab" aria-selected={year === "all"} className={year === "all" ? "active" : ""} onClick={() => setYear("all")}>Toutes <span>{dashboard.data.ues.length}</span></button>{years.map((item) => <button key={item.year} type="button" role="tab" aria-selected={year === item.year} className={year === item.year ? "active" : ""} onClick={() => setYear(item.year)}>{item.label} <span>{item.ue_count}</span></button>)}</div>
        <div className="weighting-note"><BookOpenCheck size={18} /><span><strong>Pondération ECTS active</strong>La moyenne et le GPA généraux utilisent les crédits de chaque UE.</span></div>
      </section>

      <section className="grade-scale-band" aria-label="Échelle des grades">{dashboard.data.grade_scale.map((item) => <div key={item.grade}><GradeBadge grade={item.grade} /><span>{item.description}</span><strong>{formatNumber(item.gpa)}</strong></div>)}</section>

      <section className="data-section">
        <header className="section-heading"><div><h2>Unités d'enseignement</h2><p>{ues.length} UE · {ues.filter((ue) => ue.validated).length} validées</p></div></header>
        {ues.length ? <div className="table-wrap"><table className="data-table ue-table"><thead><tr><th>Unité d'enseignement</th><th>Année</th><th>Moyenne</th><th>Grade</th><th>GPA</th><th>ECTS</th><th>État</th>{role !== "viewer" && <th><span className="sr-only">Actions</span></th>}</tr></thead><tbody>{ues.map((ue) => <tr key={ue.code} className={ue.credits_ects === null ? "needs-data" : ""}><td><div className="cell-primary"><strong className="code-label">{ue.code}</strong><span>{ue.title || `${ue.note_count} note${ue.note_count > 1 ? "s" : ""}`}</span></div></td><td data-label="Année">{yearLabel(ue.year)}</td><td data-label="Moy."><strong>{formatNumber(ue.average, " /20")}</strong></td><td data-label="Grade"><GradeBadge grade={ue.grade} description={ue.grade_description} /></td><td data-label="GPA"><strong>{formatNumber(ue.gpa, " /4")}</strong></td><td data-label="ECTS">{ue.credits_ects === null ? <span className="missing-value"><TriangleAlert size={15} /> À saisir</span> : <strong>{formatNumber(ue.credits_ects)}</strong>}</td><td data-label="État">{ue.average === null ? <span className="status-pill neutral"><CircleDashed size={14} /> À compléter</span> : ue.used_resit ? <span className="status-pill warning">Rattrapage</span> : ue.validated ? <span className="status-pill success"><CheckCircle2 size={14} /> Validée</span> : <span className="status-pill danger">Non validée</span>}</td>{role !== "viewer" && <td><button className="icon-button" type="button" onClick={() => setEditing(ue)} aria-label={`Configurer ${ue.code}`} title="Configurer"><Pencil size={17} /></button></td>}</tr>)}</tbody></table></div> : <EmptyState title="Aucune UE" detail="Les UE apparaîtront après une synchronisation PASS." />}
      </section>
      {editing && <UeEditor ue={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
