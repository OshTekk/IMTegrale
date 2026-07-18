import FullCalendar, { type CalendarRef, type DatesSetInfo, type EventClickInfo } from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/react/daygrid";
import frLocale from "@fullcalendar/react/locales/fr";
import listPlugin from "@fullcalendar/react/list";
import timeGridPlugin from "@fullcalendar/react/timegrid";
import formaThemePlugin from "@fullcalendar/react/themes/forma";
import {
  BadgeCheck,
  Building2,
  CalendarCheck2,
  CalendarDays,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clock3,
  ExternalLink,
  GraduationCap,
  Info,
  Link2,
  List,
  LoaderCircle,
  LockKeyhole,
  MapPin,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import {
  type CSSProperties,
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { ApiError } from "../lib/api";
import { formatDate, relativeDate } from "../lib/format";
import {
  queryKeys,
  useCalendarEvents,
  useCalendarStatus,
  useConnectCalendar,
  useDisconnectCalendar,
  useFipTrainingCalendar,
} from "../lib/queries";
import { useQueryClient } from "@tanstack/react-query";
import type {
  CalendarEventItem,
  CalendarStatus,
  FipTrainingCalendar,
  FipTrainingPeriod,
  FipTrainingPromotion,
} from "../types";

import "@fullcalendar/react/skeleton.css";
import "@fullcalendar/react/themes/forma/theme.css";
import "@fullcalendar/react/themes/forma/palettes/green.css";

type CalendarSection = "courses" | "training";
type CalendarView = "dayGridMonth" | "timeGridWeek" | "listMonth";
type TimelineStyle = CSSProperties & {
  "--timeline-left": string;
  "--timeline-width": string;
};

const DAY_MS = 86_400_000;
const viewOptions: Array<{ value: CalendarView; label: string; icon: typeof CalendarDays }> = [
  { value: "dayGridMonth", label: "Mois", icon: CalendarDays },
  { value: "timeGridWeek", label: "Semaine", icon: CalendarCheck2 },
  { value: "listMonth", label: "Liste", icon: List },
];

function plainDateValue(value: string): number {
  return Date.parse(`${value}T00:00:00Z`);
}

function formatPlainDate(value: string, options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }): string {
  return new Intl.DateTimeFormat("fr-FR", { ...options, timeZone: "UTC" }).format(
    new Date(`${value}T12:00:00Z`),
  );
}

function formatPlainRange(start: string, end: string): string {
  const startDate = new Date(`${start}T12:00:00Z`);
  const endDate = new Date(`${end}T12:00:00Z`);
  const sameYear = startDate.getUTCFullYear() === endDate.getUTCFullYear();
  return `${formatPlainDate(start, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) })} – ${formatPlainDate(end, { day: "numeric", month: "short", year: "numeric" })}`;
}

function toTimelineStyle(start: string, end: string, rangeStart: number, rangeEnd: number): TimelineStyle {
  const duration = rangeEnd - rangeStart + DAY_MS;
  const left = ((plainDateValue(start) - rangeStart) / duration) * 100;
  const width = ((plainDateValue(end) - plainDateValue(start) + DAY_MS) / duration) * 100;
  return {
    "--timeline-left": `${Math.max(0, left)}%`,
    "--timeline-width": `${Math.min(100 - left, width)}%`,
  };
}

function calendarErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return "Le calendrier n'a pas pu être connecté.";
  if (error.code === "CALENDAR_ACCOUNT_MISMATCH") {
    return "Ce lien appartient à un autre identifiant IMT.";
  }
  if (error.code === "CALENDAR_FETCH_COOLDOWN") {
    return error.availableAt
      ? `Nouvel essai possible ${relativeDate(error.availableAt)}.`
      : "Patiente avant de réessayer.";
  }
  return error.message;
}

function syncErrorMessage(code: string | null): string {
  if (code === "CALENDAR_SECRET_INVALID") return "Le lien doit être reconnecté.";
  if (code === "CALENDAR_LINK_REJECTED") return "Le lien n'est plus accepté par INPASS.";
  if (code === "CALENDAR_FEED_INVALID") return "INPASS a renvoyé un agenda illisible.";
  return "La dernière actualisation a échoué. Les cours déjà importés restent disponibles.";
}

function eventDateLabel(event: CalendarEventItem): string {
  if (event.all_day) {
    const start = formatPlainDate(event.start, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const exclusiveEnd = new Date(`${event.end}T00:00:00Z`);
    exclusiveEnd.setUTCDate(exclusiveEnd.getUTCDate() - 1);
    const finalDay = exclusiveEnd.toISOString().slice(0, 10);
    return event.start === finalDay ? `${start} · toute la journée` : `${start} – ${formatPlainDate(finalDay, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}`;
  }
  const start = new Date(event.start);
  const end = new Date(event.end);
  const date = new Intl.DateTimeFormat("fr-FR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Paris",
  }).format(start);
  const time = new Intl.DateTimeFormat("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Paris",
  });
  return `${date} · ${time.format(start)} – ${time.format(end)}`;
}

function CalendarConnectionModal({
  open,
  configured,
  onClose,
}: {
  open: boolean;
  configured: boolean;
  onClose: () => void;
}) {
  const connect = useConnectCalendar();
  const { showToast } = useToast();
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (open) {
      setUrl("");
      connect.reset();
    }
  }, [open]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    connect.mutate(url.trim(), {
      onSuccess: () => {
        showToast(configured ? "Lien calendrier remplacé" : "Agenda connecté");
        onClose();
      },
    });
  };

  return (
    <Modal
      open={open}
      title={configured ? "Remplacer le lien INPASS" : "Connecter ton agenda"}
      description="Le premier import démarre après la validation du lien."
      onClose={onClose}
      size="large"
    >
      <form className="modal-form calendar-connect-form" onSubmit={submit}>
        <label htmlFor="calendar-feed-url">
          Lien iCalendar INPASS
          <input
            id="calendar-feed-url"
            name="calendar-feed-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            value={url}
            placeholder="https://inpass.imt-atlantique.fr/passcal/getics?…"
            onChange={(event) => setUrl(event.target.value)}
            required
            maxLength={1024}
            aria-describedby="calendar-link-help"
          />
        </label>
        <p className="field-help" id="calendar-link-help">
          Utilise le lien privé d'export de ton agenda, associé à ton identifiant IMT.
        </p>

        <div className="calendar-secret-notice">
          <LockKeyhole size={19} />
          <div>
            <strong>Un secret, pas une simple adresse</strong>
            <p>Il est chiffré au repos, jamais réaffiché et supprimé avec ton agenda ou ton compte.</p>
          </div>
        </div>

        <details className="calendar-link-guide">
          <summary><Info size={17} /> Où trouver ce lien ?</summary>
          <ol>
            <li>Ouvre l'export de calendrier depuis INPASS.</li>
            <li>Copie le lien iCalendar complet proposé pour ton compte.</li>
            <li>Colle-le ici sans le partager avec une autre personne.</li>
          </ol>
        </details>

        {connect.isError && <p className="form-error"><CircleAlert size={16} /> {calendarErrorMessage(connect.error)}</p>}
        <div className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>Annuler</button>
          <button className="primary-button" type="submit" disabled={connect.isPending || !url.trim()}>
            {connect.isPending ? <LoaderCircle className="spin" size={17} /> : <Link2 size={17} />}
            {connect.isPending ? "Vérification…" : configured ? "Remplacer" : "Connecter"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function PersonalCalendar({ status }: { status: CalendarStatus }) {
  const queryClient = useQueryClient();
  const disconnect = useDisconnectCalendar();
  const { showToast } = useToast();
  const calendarRef = useRef<CalendarRef>(null);
  const lastSuccess = useRef(status.last_success_at);
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [visibleRange, setVisibleRange] = useState<{ start: string; end: string } | null>(null);
  const [title, setTitle] = useState("");
  const [view, setView] = useState<CalendarView>(() => (
    window.matchMedia("(max-width: 700px)").matches ? "listMonth" : "dayGridMonth"
  ));
  const [selectedEvent, setSelectedEvent] = useState<CalendarEventItem | null>(null);
  const events = useCalendarEvents(
    visibleRange?.start ?? null,
    visibleRange?.end ?? null,
    status.configured,
  );

  useEffect(() => {
    if (lastSuccess.current && status.last_success_at !== lastSuccess.current) {
      queryClient.invalidateQueries({ queryKey: queryKeys.account });
    }
    lastSuccess.current = status.last_success_at;
  }, [queryClient, status.last_success_at]);

  const calendarEvents = useMemo(() => (events.data ?? []).map((event) => ({
    id: event.id,
    title: event.title,
    start: event.start,
    end: event.end,
    allDay: event.all_day,
    extendedProps: { location: event.location },
  })), [events.data]);

  const datesSet = (info: DatesSetInfo) => {
    setTitle(info.view.title);
    setVisibleRange({ start: info.start.toISOString(), end: info.end.toISOString() });
  };

  const changeView = (next: CalendarView) => {
    setView(next);
    calendarRef.current?.getApi().changeView(next);
  };

  const selectEvent = (info: EventClickInfo) => {
    const source = events.data?.find((event) => event.id === info.event.id);
    if (source) setSelectedEvent(source);
  };

  const removeCalendar = () => {
    disconnect.mutate(undefined, {
      onSuccess: () => {
        setDisconnectOpen(false);
        setVisibleRange(null);
        showToast("Agenda et lien privé supprimés");
      },
      onError: (error) => showToast(error.message, "error"),
    });
  };

  if (!status.configured) {
    return (
      <section className="calendar-onboarding data-section">
        <header className="section-heading">
          <div><span className="section-kicker">Agenda personnel</span><h2>Retrouve tes cours dans IMTégrale</h2></div>
          <span className="official-data-label"><ShieldCheck size={16} /> Privé</span>
        </header>
        <div className="calendar-onboarding-body">
          <EmptyState
            icon={<CalendarPlus size={25} />}
            title="Ton agenda n'est pas encore connecté"
            detail="Ajoute ton lien iCalendar INPASS une seule fois. Les cours seront ensuite actualisés automatiquement chaque heure."
            action={<button className="primary-button" type="button" onClick={() => setConnectionOpen(true)}><Link2 size={17} /> Ajouter mon lien</button>}
          />
          <dl className="calendar-privacy-facts">
            <div><dt><LockKeyhole size={18} /> Lien protégé</dt><dd>Chiffré et jamais affiché après l'enregistrement.</dd></div>
            <div><dt><RefreshCw size={18} /> Toutes les heures</dt><dd>Une seule récupération planifiée par compte.</dd></div>
            <div><dt><Trash2 size={18} /> Révocable</dt><dd>Le lien et tous les cours importés sont supprimables immédiatement.</dd></div>
          </dl>
        </div>
        <CalendarConnectionModal open={connectionOpen} configured={false} onClose={() => setConnectionOpen(false)} />
      </section>
    );
  }

  return (
    <div className="calendar-personal-stack">
      <section className={`calendar-sync-band ${status.last_status === "error" ? "has-error" : ""}`}>
        <div className="calendar-sync-state">
          <span>{status.last_status === "error" ? <CircleAlert size={20} /> : <CalendarCheck2 size={20} />}</span>
          <div>
            <strong>{status.last_status === "error" ? "Actualisation à vérifier" : "Agenda INPASS connecté"}</strong>
            <p>{status.last_status === "error" ? syncErrorMessage(status.last_error_code) : `${status.event_count} cours importés · ${status.account_hint ?? "compte IMT"}`}</p>
          </div>
        </div>
        <dl>
          <div><dt>Dernière actualisation</dt><dd>{status.last_success_at ? relativeDate(status.last_success_at) : "En attente"}</dd></div>
          <div><dt>Prochaine vérification</dt><dd>{status.next_refresh_at ? relativeDate(status.next_refresh_at) : "Dans l'heure"}</dd></div>
        </dl>
        <button className="secondary-button" type="button" onClick={() => setConnectionOpen(true)}><Settings2 size={17} /> Gérer</button>
      </section>

      <section className="personal-calendar data-section" aria-busy={events.isFetching}>
        <header className="calendar-toolbar">
          <div className="calendar-period-controls">
            <button className="icon-button" type="button" onClick={() => calendarRef.current?.getApi().prev()} aria-label="Période précédente" title="Période précédente"><ChevronLeft size={19} /></button>
            <button className="secondary-button calendar-today-button" type="button" onClick={() => calendarRef.current?.getApi().today()}>Aujourd'hui</button>
            <button className="icon-button" type="button" onClick={() => calendarRef.current?.getApi().next()} aria-label="Période suivante" title="Période suivante"><ChevronRight size={19} /></button>
          </div>
          <h2 aria-live="polite">{title || "Agenda"}</h2>
          <div className="calendar-view-switch segmented-control" role="tablist" aria-label="Vue de l'agenda">
            {viewOptions.map((option) => <button key={option.value} type="button" role="tab" aria-selected={view === option.value} className={view === option.value ? "active" : ""} onClick={() => changeView(option.value)} title={option.label}><option.icon size={16} /><span>{option.label}</span></button>)}
          </div>
        </header>
        {events.isFetching && <span className="calendar-loading-line" aria-hidden="true" />}
        {events.isError && <div className="calendar-inline-error"><CircleAlert size={17} /> {events.error.message}</div>}
        {!events.isFetching && !events.isError && events.data?.length === 0 && status.event_count > 0 && <div className="calendar-range-empty"><Info size={16} /> Aucun cours importé sur cette période.</div>}
        <div className="fullcalendar-frame">
          <FullCalendar
            ref={calendarRef}
            plugins={[dayGridPlugin, timeGridPlugin, listPlugin, formaThemePlugin]}
            locale={frLocale}
            initialView={view}
            headerToolbar={false}
            firstDay={1}
            height="auto"
            expandRows
            nowIndicator
            weekNumbers
            dayMaxEvents={3}
            moreLinkClick="popover"
            allDaySlot
            slotMinTime="07:00:00"
            slotMaxTime="21:00:00"
            scrollTime="08:00:00"
            displayEventEnd
            eventTimeFormat={{ hour: "2-digit", minute: "2-digit", meridiem: false }}
            events={calendarEvents}
            datesSet={datesSet}
            eventClick={selectEvent}
          />
        </div>
      </section>

      <CalendarConnectionModal open={connectionOpen} configured onClose={() => setConnectionOpen(false)} />
      <Modal open={disconnectOpen} title="Supprimer l'agenda ?" description="Cette action retire le lien privé et tous les cours importés." onClose={() => setDisconnectOpen(false)} size="small">
        <div className="calendar-disconnect-dialog">
          <p>Tu pourras reconnecter un lien plus tard. Aucune donnée académique PASS n'est affectée.</p>
          <div className="modal-actions">
            <button className="secondary-button" type="button" onClick={() => setDisconnectOpen(false)}>Annuler</button>
            <button className="danger-button" type="button" onClick={removeCalendar} disabled={disconnect.isPending}>{disconnect.isPending ? <LoaderCircle className="spin" size={17} /> : <Trash2 size={17} />} Supprimer</button>
          </div>
        </div>
      </Modal>
      <Modal open={selectedEvent !== null} title={selectedEvent?.title ?? "Cours"} description="Détail importé depuis INPASS" onClose={() => setSelectedEvent(null)} size="small">
        {selectedEvent && <div className="calendar-event-detail"><div><Clock3 size={19} /><span><strong>Horaire</strong><p>{eventDateLabel(selectedEvent)}</p></span></div><div><MapPin size={19} /><span><strong>Lieu</strong><p>{selectedEvent.location || "Non indiqué dans INPASS"}</p></span></div></div>}
      </Modal>
      <div className="calendar-data-controls">
        <span><LockKeyhole size={16} /> Cet agenda n'est jamais accessible depuis un token de partage.</span>
        <button type="button" onClick={() => setDisconnectOpen(true)}><Trash2 size={15} /> Supprimer mon agenda</button>
      </div>
    </div>
  );
}

function monthMarkers(rangeStart: number, rangeEnd: number): Array<{ label: string; left: number }> {
  const current = new Date(rangeStart);
  current.setUTCDate(1);
  const markers: Array<{ label: string; left: number }> = [];
  const duration = rangeEnd - rangeStart + DAY_MS;
  while (current.getTime() <= rangeEnd) {
    const followingMonth = new Date(current);
    followingMonth.setUTCMonth(followingMonth.getUTCMonth() + 1);
    const partialMonthDays = (followingMonth.getTime() - rangeStart) / DAY_MS;
    if (current.getTime() >= rangeStart || partialMonthDays >= 14) {
      markers.push({
        label: new Intl.DateTimeFormat("fr-FR", { month: "short", timeZone: "UTC" }).format(current),
        left: Math.max(0, ((current.getTime() - rangeStart) / duration) * 100),
      });
    }
    current.setUTCMonth(current.getUTCMonth() + 1);
  }
  return markers;
}

function TrainingCalendarView({ calendar }: { calendar: FipTrainingCalendar }) {
  const fallbackPromotion = calendar.promotions[0]?.promotion_year ?? null;
  const [promotionYear, setPromotionYear] = useState(calendar.default_promotion_year ?? fallbackPromotion);
  const promotion = calendar.promotions.find((item) => item.promotion_year === promotionYear) ?? calendar.promotions[0];

  if (!promotion) return <EmptyState title="Calendrier indisponible" detail="Aucune promotion ne figure dans le calendrier source." />;

  const starts = [...promotion.periods.map((item) => item.start), ...promotion.semesters.map((item) => item.start)];
  const ends = [...promotion.periods.map((item) => item.end), ...promotion.semesters.map((item) => item.end)];
  const rangeStart = Math.min(...starts.map(plainDateValue));
  const rangeEnd = Math.max(...ends.map(plainDateValue));
  const markers = monthMarkers(rangeStart, rangeEnd);
  const today = new Date().toISOString().slice(0, 10);
  const activePeriod = promotion.periods.find((period) => period.start <= today && period.end >= today);
  const nextPeriod = promotion.periods.find((period) => period.start > today);
  const statePeriod = activePeriod ?? nextPeriod;

  return (
    <div className="training-calendar-stack">
      <section className="training-heading-band">
        <div>
          <span className="section-kicker">Alternance 2026–2027</span>
          <h2>{calendar.speciality}</h2>
          <p>Toutes les promotions FIP réunies dans un calendrier lisible.</p>
        </div>
        <div className="training-current-state">
          <span>{statePeriod?.kind === "school" ? <GraduationCap size={20} /> : <Building2 size={20} />}</span>
          <div><small>{activePeriod ? "Période actuelle" : "Prochaine période"}</small><strong>{activePeriod ? (activePeriod.kind === "school" ? "Formation" : "Entreprise") : nextPeriod ? `${nextPeriod.kind === "school" ? "Formation" : "Entreprise"} · ${formatPlainDate(nextPeriod.start, { day: "numeric", month: "long" })}` : "Année terminée"}</strong></div>
        </div>
      </section>

      <section className="training-promotion-selector" aria-label="Choisir une promotion FIP">
        <div className="segmented" role="tablist" aria-label="Promotion">
          {calendar.promotions.map((item) => <button key={item.promotion_year} type="button" role="tab" aria-selected={promotion.promotion_year === item.promotion_year} className={promotion.promotion_year === item.promotion_year ? "active" : ""} onClick={() => setPromotionYear(item.promotion_year)}><span>FIP {item.promotion_year}</span><small>{item.level}</small></button>)}
        </div>
        {calendar.default_promotion_year === promotion.promotion_year && <span className="current-promotion-label"><BadgeCheck size={15} /> Ta promotion</span>}
      </section>

      <section className="training-timeline data-section">
        <header className="section-heading">
          <div><span className="section-kicker">Vue annuelle</span><h2>FIP {promotion.promotion_year} · {promotion.level}</h2></div>
          <div className="training-legend"><span className="school"><i /> Formation</span><span className="company"><i /> Entreprise</span>{promotion.milestones.length > 0 && <span className="mobility"><i /> International</span>}</div>
        </header>
        <div className="training-timeline-chart">
          <div className="training-month-axis" aria-hidden="true">{markers.map((marker) => <span key={`${marker.label}-${marker.left}`} style={{ left: `${marker.left}%` }}>{marker.label}</span>)}</div>
          <div className="training-track" aria-label="Périodes de formation et d'entreprise">
            {promotion.periods.map((period) => <span key={`${period.kind}-${period.start}`} className={`training-block ${period.kind}`} style={toTimelineStyle(period.start, period.end, rangeStart, rangeEnd)} title={`${period.kind === "school" ? "Formation" : "Entreprise"} · ${formatPlainRange(period.start, period.end)}`}><i>{period.weeks} sem.</i>{period.campus && <b>{period.campus}</b>}</span>)}
          </div>
          <div className="semester-track" aria-label="Semestres">{promotion.semesters.map((semester) => <span key={semester.semester} style={toTimelineStyle(semester.start, semester.end, rangeStart, rangeEnd)}><strong>{semester.semester}</strong><small>{formatPlainRange(semester.start, semester.end)}</small></span>)}</div>
          {promotion.milestones.length > 0 && <div className="milestone-track" aria-label="Périodes internationales">{promotion.milestones.map((milestone) => <span key={milestone.kind} className={milestone.kind} style={toTimelineStyle(milestone.start, milestone.end, rangeStart, rangeEnd)}><ExternalLink size={14} /> {milestone.kind === "international_project" ? "PSI" : "Mobilité"}</span>)}</div>}
        </div>
      </section>

      <section className="training-details data-section">
        <header className="section-heading"><div><span className="section-kicker">Périodes</span><h2>Dates de la promotion {promotion.promotion_year}</h2></div><div className="training-totals"><span><GraduationCap size={16} /> {promotion.totals.school_weeks} sem.</span><span><Building2 size={16} /> {promotion.totals.company_weeks} sem.</span></div></header>
        <div className="training-period-list">{promotion.periods.map((period) => <TrainingPeriodRow key={`${period.kind}-${period.start}`} period={period} />)}</div>
        {promotion.milestones.length > 0 && <div className="training-milestones">{promotion.milestones.map((milestone) => <div key={milestone.kind}><span><ExternalLink size={18} /></span><div><strong>{milestone.title}</strong><p>{formatPlainRange(milestone.start, milestone.end)} · {milestone.detail}</p></div></div>)}</div>}
      </section>

      <footer className="training-source-note"><Info size={16} /><span><strong>{calendar.source.label}</strong>Version du {formatPlainDate(calendar.source.version_date, { day: "numeric", month: "long", year: "numeric" })}. {calendar.campus_note}</span></footer>
    </div>
  );
}

function TrainingPeriodRow({ period }: { period: FipTrainingPeriod }) {
  const school = period.kind === "school";
  return <div className="training-period-row"><span className={school ? "school" : "company"}>{school ? <GraduationCap size={18} /> : <Building2 size={18} />}</span><div><strong>{school ? "Formation" : "Entreprise"}</strong><p>{formatPlainRange(period.start, period.end)}</p></div><span className="training-week-count">{period.weeks} semaines</span>{period.campus && <span className="campus-badge"><MapPin size={14} /> {period.campus}</span>}</div>;
}

export function CalendarPage() {
  const status = useCalendarStatus();
  const [section, setSection] = useState<CalendarSection>("courses");
  const training = useFipTrainingCalendar(
    status.data?.fip_training_available === true && section === "training",
  );

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [section]);

  if (status.isPending) return <div className="calendar-page-skeleton"><div className="skeleton" /><div className="skeleton" /></div>;
  if (status.isError || !status.data) return <div className="error-panel"><CircleAlert size={22} /> {status.error?.message ?? "L'agenda est indisponible."}</div>;

  return (
    <div className="page-stack calendar-page">
      {status.data.fip_training_available && <section className="calendar-section-tabs">
        <div className="segmented" role="tablist" aria-label="Type de calendrier">
          <button type="button" role="tab" aria-selected={section === "courses"} className={section === "courses" ? "active" : ""} onClick={() => setSection("courses")}><CalendarDays size={17} /> Mes cours</button>
          <button type="button" role="tab" aria-selected={section === "training"} className={section === "training" ? "active" : ""} onClick={() => setSection("training")}><GraduationCap size={17} /> Formation FIP</button>
        </div>
        <span><ShieldCheck size={16} /> L'agenda de cours reste strictement personnel.</span>
      </section>}

      {section === "courses" && <PersonalCalendar status={status.data} />}
      {section === "training" && status.data.fip_training_available && (
        training.isPending
          ? <div className="table-skeleton skeleton" />
          : training.isError || !training.data
            ? <div className="error-panel"><CircleAlert size={22} /> {training.error?.message ?? "Le calendrier de formation est indisponible."}</div>
            : <TrainingCalendarView calendar={training.data} />
      )}
    </div>
  );
}
