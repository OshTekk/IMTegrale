import { ArrowRight, Award, BookOpenCheck, CalendarRange, CircleCheck, CircleGauge, Clock3, GraduationCap, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { GradeBadge } from "../components/GradeBadge";
import { eventLabel, formatNumber, relativeDate } from "../lib/format";
import { useDashboard } from "../lib/queries";

function LoadingOverview() {
  return <div className="overview-grid"><div className="skeleton metric-skeleton" /><div className="skeleton metric-skeleton" /><div className="skeleton metric-skeleton" /><div className="skeleton metric-skeleton" /><div className="skeleton panel-skeleton wide" /><div className="skeleton panel-skeleton" /></div>;
}

export function OverviewPage() {
  const dashboard = useDashboard();
  const [semesterKey, setSemesterKey] = useState("");
  if (dashboard.isPending) return <LoadingOverview />;
  if (dashboard.isError || !dashboard.data) return <div className="error-panel"><TriangleAlert size={22} /><div><h2>Données indisponibles</h2><p>{dashboard.error?.message}</p></div><button className="secondary-button" onClick={() => dashboard.refetch()}>Réessayer</button></div>;

  const data = dashboard.data;
  const allocatedCredits = data.ues.reduce((sum, item) => sum + (item.credits_ects ?? 0), 0);
  const progress = allocatedCredits > 0 ? Math.min(100, Math.round((data.summary.validated_credits / allocatedCredits) * 100)) : 0;
  const chart = data.ues.filter((ue) => ue.average !== null).map((ue) => ({ name: ue.code, moyenne: ue.average, validated: ue.validated }));
  const maxGradeCount = Math.max(1, ...data.grade_distribution.map((item) => item.count));
  const selectedSemester = data.semesters.find((item) => item.semester === semesterKey) ?? data.semesters[0];

  return (
    <div className="overview-grid">
      {data.summary.missing_ects_count > 0 && <Link className="attention-banner" to="/ues"><TriangleAlert size={18} /><span><strong>{data.summary.missing_ects_count} UE sans crédits ECTS</strong>Actualise les données IMT ou complète les valeurs encore absentes.</span><ArrowRight size={18} /></Link>}

      <section className="metric-grid" aria-label="Indicateurs principaux">
        <article className="metric-card average"><div className="metric-label"><span><GraduationCap size={18} /></span>Moyenne générale</div><strong>{formatNumber(data.summary.average, " /20")}</strong><p>{formatNumber(data.summary.average_credits, " ECTS")} pondérés</p></article>
        <article className="metric-card gpa"><div className="metric-label"><span><Award size={18} /></span>GPA général</div><strong>{formatNumber(data.summary.gpa, " /4")}</strong><p>Calcul automatique par UE</p></article>
        <article className="metric-card credits"><div className="metric-label"><span><CircleCheck size={18} /></span>ECTS validés</div><div className="metric-progress"><strong>{formatNumber(data.summary.validated_credits)}</strong><svg viewBox="0 0 44 44" aria-label={`${progress}% des crédits alloués validés`}><circle cx="22" cy="22" r="18" /><circle className="progress" cx="22" cy="22" r="18" pathLength="100" strokeDasharray={`${progress} 100`} /></svg></div><p>{progress}% des crédits renseignés</p></article>
        <article className="metric-card ues"><div className="metric-label"><span><BookOpenCheck size={18} /></span>Unités d'enseignement</div><strong>{data.summary.ue_count}</strong><p>{data.summary.note_count} notes actives</p></article>
      </section>

      {selectedSemester && <section className="semester-overview-band"><header><span><CalendarRange size={19} /></span><div><small>Progression académique</small><h2>Résultats par semestre</h2></div><div className="segmented semester-overview-tabs" role="tablist" aria-label="Choisir un semestre">{data.semesters.map((item) => <button key={item.semester} type="button" role="tab" aria-selected={selectedSemester.semester === item.semester} className={selectedSemester.semester === item.semester ? "active" : ""} onClick={() => setSemesterKey(item.semester)}>{item.semester}</button>)}</div></header><div className="semester-overview-values"><div><span>Moyenne PASS</span><strong>{formatNumber(selectedSemester.average, " /20")}</strong><small>{formatNumber(selectedSemester.average_credits, " ECTS pondérés")}</small></div><div><span>GPA</span><strong>{formatNumber(selectedSemester.gpa, " /4")}</strong><small>{formatNumber(selectedSemester.gpa_credits, " ECTS pondérés")}</small></div><div><span>Crédits obtenus</span><strong>{formatNumber(selectedSemester.validated_credits)}</strong><small>{selectedSemester.ue_count} UE officielles</small></div></div></section>}

      <section className="content-panel performance-panel">
        <header className="panel-heading"><div><span>Performance</span><h2>Moyenne par UE</h2></div><Link to="/ues">Voir les UE <ArrowRight size={16} /></Link></header>
        {chart.length ? <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><BarChart data={chart} margin={{ top: 12, right: 8, left: -24, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e3e7e2" /><XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fill: "#66716c", fontSize: 11 }} interval="preserveStartEnd" minTickGap={16} tickMargin={9} /><YAxis domain={[0, 20]} ticks={[0, 5, 10, 15, 20]} tickLine={false} axisLine={false} tick={{ fill: "#83908a", fontSize: 11 }} /><Tooltip cursor={{ fill: "#f0f3ef" }} contentStyle={{ borderRadius: 6, border: "1px solid #dfe4df", boxShadow: "0 8px 24px rgba(20,38,31,.08)" }} formatter={(value) => [`${Number(value).toLocaleString("fr-FR")} /20`, "Moyenne"]} /><ReferenceLine y={10} stroke="#de765e" strokeDasharray="4 4" /><Bar dataKey="moyenne" fill="#2b7560" radius={[3, 3, 0, 0]} maxBarSize={34} /></BarChart></ResponsiveContainer></div> : <div className="chart-empty"><CircleGauge size={24} /><span>Les moyennes apparaîtront après la première synchronisation.</span></div>}
      </section>

      <section className="content-panel grade-panel">
        <header className="panel-heading"><div><span>Répartition</span><h2>Grades obtenus</h2></div></header>
        <div className="grade-distribution">
          {data.grade_distribution.map((item) => <div className="grade-row" key={item.grade}><GradeBadge grade={item.grade} /><div className="grade-track"><span style={{ width: `${(item.count / maxGradeCount) * 100}%` }} /></div><strong>{item.count}</strong></div>)}
        </div>
        <div className="grade-scale-mini">{data.grade_scale.map((item) => <span key={item.grade}><GradeBadge grade={item.grade} /><small>{formatNumber(item.gpa)}</small></span>)}</div>
      </section>

      <section className="content-panel recent-panel">
        <header className="panel-heading"><div><span>Derniers résultats</span><h2>Notes récentes</h2></div><Link to="/notes">Tout afficher <ArrowRight size={16} /></Link></header>
        <div className="recent-notes">{data.notes.slice(0, 6).map((note) => <div className="recent-note" key={note.id}><div className="ue-symbol">{note.ue_code.slice(0, 3)}</div><div><strong>{note.label}</strong><span>{note.ue_code} · coeff. {formatNumber(note.coefficient)}</span></div><b>{formatNumber(note.score)}<small>/20</small></b></div>)}</div>
      </section>

      <section className="content-panel activity-panel">
        <header className="panel-heading"><div><span>Journal</span><h2>Activité récente</h2></div></header>
        <div className="activity-list">{data.events.slice(0, 8).map((event) => <div className="activity-row" key={event.id}><span className="activity-icon"><Clock3 size={15} /></span><div><strong>{eventLabel(event.kind, event.payload)}</strong><small>{relativeDate(event.created_at)}</small></div></div>)}</div>
      </section>
    </div>
  );
}
