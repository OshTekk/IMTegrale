import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeftRight,
  BadgeCheck,
  BarChart3,
  Check,
  ChevronDown,
  CloudOff,
  Copy,
  EllipsisVertical,
  FilePlus2,
  FlaskConical,
  History,
  Info,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { EmptyState } from "../components/EmptyState";
import { GradeBadge } from "../components/GradeBadge";
import { Modal } from "../components/Modal";
import {
  SimulationConfirmationModal as ConfirmationModal,
  type SimulationConfirmation as Confirmation,
} from "../components/simulations/SimulationConfirmationModal";
import {
  SimulationSaveIndicator as SaveIndicator,
  type SimulationSaveState as SaveState,
} from "../components/simulations/SimulationSaveIndicator";
import { useToast } from "../components/Toast";
import {
  noteSimulationsResolveAssessmentConflict,
  noteSimulationsResolveUeConflict,
  noteSimulationsScenarioCompare,
  noteSimulationsScenarioCreate,
  noteSimulationsScenarioDelete,
  noteSimulationsScenarioDuplicate,
  noteSimulationsScenarioRebase,
  noteSimulationsScenarioReset,
  noteSimulationsScenarioSave,
} from "../generated/api/sdk.gen";
import { ApiError } from "../lib/api";
import { formatDate, formatNumber, relativeDate } from "../lib/format";
import { apiData, throwOnApiError } from "../lib/generatedApi";
import {
  calculateNoteDraftProjection,
  calculateNoteUeProjection,
  createNoteAssessment,
  createNoteUe,
  mergeSavedNoteIds,
  noteDraftIsValid,
  noteScenarioToDraft,
  noteSimulationPayload,
  noteSimulationSentKeys,
  noteUeDisplayName,
  type NoteSimulationAssessmentDraft,
  type NoteSimulationDraft,
  type NoteSimulationUeDraft,
} from "../lib/noteSimulations";
import { queryKeys, useNoteSimulation, useNoteSimulations, useSession } from "../lib/queries";
import { SIMULATION_SEMESTERS } from "../lib/simulations";
import type {
  NoteSimulationList,
  NoteSimulationScenario,
  NoteSimulationScenarioSummary,
  SimulationSemester,
} from "../types";

type Resolution = "source" | "simulation";

const comparisonFieldLabels = {
  presence: "Présence",
  semester: "Semestre",
  ue: "UE",
  credits_ects: "ECTS",
  assessments: "Évaluations",
} as const;

function scenarioSummary(scenario: NoteSimulationScenario): NoteSimulationScenarioSummary {
  const { ues: _ues, ...summary } = scenario;
  return summary;
}

function numberOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

function ueNature(ue: NoteSimulationUeDraft): "imported" | "modified" | "simulated" {
  if (!ue.server || ue.server.nature === "simulated") return "simulated";
  const baseline = ue.server.baseline;
  if (!baseline) return "imported";
  const unchanged =
    ue.semester === baseline.semester &&
    (ue.ue_code || null) === baseline.ue_code &&
    ue.title === (baseline.title ?? "") &&
    numberOrNull(ue.credits_ects) === baseline.credits_ects;
  return unchanged ? "imported" : "modified";
}

function assessmentNature(assessment: NoteSimulationAssessmentDraft): "imported" | "modified" | "simulated" {
  if (!assessment.server || assessment.server.nature === "simulated") return "simulated";
  const baseline = assessment.server.baseline;
  if (!baseline) return "imported";
  const unchanged =
    assessment.label === (baseline.label ?? "") &&
    numberOrNull(assessment.score) === baseline.score &&
    numberOrNull(assessment.coefficient) === baseline.coefficient &&
    assessment.is_resit === Boolean(baseline.is_resit);
  return unchanged ? "imported" : "modified";
}

function natureLabel(nature: "imported" | "modified" | "simulated") {
  if (nature === "imported") return "Officielle importée";
  if (nature === "modified") return "Hypothèse modifiée";
  return "Valeur simulée";
}

function NaturePill({ nature }: { nature: "imported" | "modified" | "simulated" }) {
  return (
    <span className={`simulation-origin-pill ${nature}`}>
      {nature === "imported" ? (
        <BadgeCheck size={13} />
      ) : nature === "modified" ? (
        <Sparkles size={13} />
      ) : (
        <FlaskConical size={13} />
      )}
      {natureLabel(nature)}
    </span>
  );
}

function CreationModal({
  open,
  sourceUeCount,
  sourceAssessmentCount,
  pending,
  onClose,
  onCreate,
}: {
  open: boolean;
  sourceUeCount: number;
  sourceAssessmentCount: number;
  pending: boolean;
  onClose: () => void;
  onCreate: (name: string, importCurrent: boolean) => void;
}) {
  const [name, setName] = useState("Projection du semestre");
  const [mode, setMode] = useState<"blank" | "academic">(sourceUeCount ? "academic" : "blank");
  useEffect(() => {
    if (!open) return;
    setName("Projection du semestre");
    setMode(sourceUeCount ? "academic" : "blank");
  }, [open, sourceUeCount]);
  return (
    <Modal
      open={open}
      title="Créer une simulation de notes"
      description="Choisis ton point de départ. Les données importées deviennent une copie librement modifiable."
      onClose={onClose}
      size="large"
    >
      <form
        className="simulation-create-form"
        onSubmit={(event) => {
          event.preventDefault();
          onCreate(name, mode === "academic");
        }}
      >
        <label className="simulation-name-field">
          <span>Nom du scénario</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={80}
            autoComplete="off"
            required
          />
        </label>
        <div className="simulation-start-options" role="radiogroup" aria-label="Point de départ">
          <button
            type="button"
            className={mode === "academic" ? "active" : ""}
            onClick={() => setMode("academic")}
            disabled={!sourceUeCount}
            role="radio"
            aria-checked={mode === "academic"}
          >
            <span>
              <BarChart3 size={21} />
            </span>
            <strong>Importer mes notes</strong>
            <small>
              {sourceUeCount
                ? `${sourceUeCount} UE · ${sourceAssessmentCount} évaluations`
                : "Aucune donnée académique disponible"}
            </small>
            <i>{mode === "academic" && <Check size={14} />}</i>
          </button>
          <button
            type="button"
            className={mode === "blank" ? "active" : ""}
            onClick={() => setMode("blank")}
            role="radio"
            aria-checked={mode === "blank"}
          >
            <span>
              <FilePlus2 size={21} />
            </span>
            <strong>Commencer à zéro</strong>
            <small>UE et évaluations entièrement libres</small>
            <i>{mode === "blank" && <Check size={14} />}</i>
          </button>
        </div>
        <div className="simulation-private-note">
          <ShieldCheck size={17} />
          <span>Privé à ton compte. Rien n’est envoyé vers PASS ou COMPETENCES.</span>
        </div>
        <footer className="modal-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            Annuler
          </button>
          <button className="primary-button" type="submit" disabled={pending || !name.trim()}>
            {pending ? <LoaderCircle className="spin" size={17} /> : <Plus size={17} />} Créer
          </button>
        </footer>
      </form>
    </Modal>
  );
}

function ComparisonModal({
  open,
  accountId,
  left,
  scenarios,
  rightId,
  setRightId,
  onClose,
}: {
  open: boolean;
  accountId: string;
  left: NoteSimulationScenarioSummary;
  scenarios: NoteSimulationScenarioSummary[];
  rightId: string;
  setRightId: (id: string) => void;
  onClose: () => void;
}) {
  const comparison = useQuery({
    queryKey: [...queryKeys.noteSimulations(accountId), "compare", left.id, rightId],
    queryFn: () =>
      apiData(
        noteSimulationsScenarioCompare({
          query: { left_id: left.id, right_id: rightId },
          throwOnError: throwOnApiError,
        }),
      ),
    enabled: open && Boolean(rightId) && rightId !== left.id,
    staleTime: 0,
  });
  const data = comparison.data;
  const warningGroups = data ? [data.left, data.right].filter((item) => item.result.warnings.length > 0) : [];
  return (
    <Modal
      open={open}
      title="Comparer deux simulations de notes"
      description="Mesure l’effet exact de tes hypothèses sur la moyenne et le GPA dérivé."
      onClose={onClose}
      size="large"
    >
      <div className="simulation-compare-controls">
        <label>
          <span>Scénario de référence</span>
          <input value={left.name} disabled />
        </label>
        <ArrowLeftRight size={18} />
        <label>
          <span>Comparer avec</span>
          <select value={rightId} onChange={(event) => setRightId(event.target.value)}>
            {scenarios
              .filter((item) => item.id !== left.id)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      {comparison.isPending ? (
        <div className="simulation-compare-loading">
          <LoaderCircle className="spin" size={21} /> Calcul de la comparaison…
        </div>
      ) : comparison.isError ? (
        <div className="inline-warning">
          <AlertTriangle size={17} /> {comparison.error.message}
        </div>
      ) : (
        data && (
          <div className="simulation-comparison note-simulation-comparison">
            <div className="simulation-comparison-score">
              <div>
                <span>{data.left.name}</span>
                <strong>{formatNumber(data.left.result.average)}</strong>
                <small>moyenne /20</small>
              </div>
              <div
                className={
                  data.average_delta === null
                    ? "neutral"
                    : data.average_delta > 0
                      ? "positive"
                      : data.average_delta < 0
                        ? "negative"
                        : "neutral"
                }
              >
                <span>Écart</span>
                <strong>
                  {data.average_delta === null
                    ? "—"
                    : `${data.average_delta > 0 ? "+" : ""}${formatNumber(data.average_delta)}`}
                </strong>
                <small>point{Math.abs(data.average_delta ?? 0) > 1 ? "s" : ""}</small>
              </div>
              <div>
                <span>{data.right.name}</span>
                <strong>{formatNumber(data.right.result.average)}</strong>
                <small>moyenne /20</small>
              </div>
            </div>
            <div className="note-comparison-gpa">
              <span>GPA dérivé</span>
              <strong>
                {formatNumber(data.left.result.gpa)} <ArrowLeftRight size={14} /> {formatNumber(data.right.result.gpa)}
              </strong>
              <small>
                {data.gpa_delta === null
                  ? "Écart indisponible"
                  : `${data.gpa_delta > 0 ? "+" : ""}${formatNumber(data.gpa_delta)} point`}
              </small>
            </div>
            {warningGroups.length > 0 && (
              <div className="note-comparison-warnings">
                <AlertTriangle size={17} />
                <div>
                  <strong>Résultats partiels ou données exclues</strong>
                  {warningGroups.map((item) => (
                    <p key={item.id}>
                      <b>{item.name}</b> ·{" "}
                      {item.result.warnings
                        .map((warning) => `${warning.message.replace(/\.$/, "")} (${warning.count})`)
                        .join(" · ")}
                    </p>
                  ))}
                </div>
              </div>
            )}
            <div className="simulation-differences">
              <header>
                <strong>
                  {data.differences.length} différence{data.differences.length === 1 ? "" : "s"}
                </strong>
                <span>UE, ECTS ou évaluations</span>
              </header>
              {data.differences.length ? (
                data.differences.map((difference) => {
                  const representative = difference.right ?? difference.left;
                  return (
                    <div key={difference.lineage_key}>
                      <span>
                        {representative ? noteUeDisplayName(representative) : "UE"}
                        <small>
                          {difference.kind === "left_only"
                            ? `Uniquement dans ${data.left.name}`
                            : difference.kind === "right_only"
                              ? `Uniquement dans ${data.right.name}`
                              : difference.fields.map((field) => comparisonFieldLabels[field]).join(" · ")}
                        </small>
                      </span>
                      <div className="note-comparison-values">
                        {difference.left ? (
                          <>
                            <strong>{formatNumber(difference.left.projection.average)}</strong>
                            <small>/20</small>
                          </>
                        ) : (
                          <i>Absente</i>
                        )}
                        <ArrowLeftRight size={14} />
                        {difference.right ? (
                          <>
                            <strong>{formatNumber(difference.right.projection.average)}</strong>
                            <small>/20</small>
                          </>
                        ) : (
                          <i>Absente</i>
                        )}
                      </div>
                    </div>
                  );
                })
              ) : (
                <EmptyState
                  icon={<Check size={20} />}
                  title="Aucun écart"
                  detail="Ces deux scénarios contiennent les mêmes hypothèses."
                />
              )}
            </div>
            <div className="simulation-comparison-method">
              <Info size={16} />
              <span>
                <strong>Règle IMTégrale {data.formula.version}</strong> · {data.formula.scale} · {data.formula.rounding}
                .
              </span>
            </div>
          </div>
        )
      )}
      <footer className="modal-actions">
        <button className="primary-button" type="button" onClick={onClose}>
          Fermer
        </button>
      </footer>
    </Modal>
  );
}

function ConflictPanel({
  label,
  disabled,
  onResolve,
}: {
  label: string;
  disabled: boolean;
  onResolve: (resolution: Resolution) => void;
}) {
  return (
    <div className="simulation-row-conflict note-simulation-conflict">
      <TriangleAlert size={18} />
      <div>
        <strong>La source officielle a changé</strong>
        <p>{label}</p>
      </div>
      <div>
        <button type="button" onClick={() => onResolve("simulation")} disabled={disabled}>
          Garder ma valeur
        </button>
        <button type="button" onClick={() => onResolve("source")} disabled={disabled}>
          Prendre la source
        </button>
      </div>
    </div>
  );
}

function AssessmentRow({
  assessment,
  disabled,
  onChange,
  onRemove,
  onResolve,
}: {
  assessment: NoteSimulationAssessmentDraft;
  disabled: boolean;
  onChange: (changes: Partial<NoteSimulationAssessmentDraft>) => void;
  onRemove: () => void;
  onResolve: (resolution: Resolution) => void;
}) {
  const nature = assessmentNature(assessment);
  const score = numberOrNull(assessment.score);
  const coefficient = numberOrNull(assessment.coefficient);
  const invalidScore = score !== null && (!Number.isFinite(score) || score < 0 || score > 20);
  const invalidCoefficient =
    coefficient === null || !Number.isFinite(coefficient) || coefficient <= 0 || coefficient > 100;
  const sourceStatus = assessment.server?.source?.status;
  return (
    <Fragment>
      <div
        className={`note-assessment-row nature-${nature}${sourceStatus === "conflict" ? " has-conflict" : ""}${!assessment.label.trim() || invalidScore || invalidCoefficient ? " is-incomplete" : ""}`}
      >
        <label className="note-assessment-label">
          <span className="sr-only">Évaluation</span>
          <input
            value={assessment.label}
            onChange={(event) => onChange({ label: event.target.value })}
            maxLength={240}
            placeholder="Nom de l’évaluation"
            disabled={disabled}
            aria-invalid={!assessment.label.trim()}
          />
        </label>
        <label className="note-assessment-score">
          <span className="sr-only">Note sur 20</span>
          <input
            type="number"
            value={assessment.score}
            onChange={(event) => onChange({ score: event.target.value })}
            min="0"
            max="20"
            step="0.01"
            inputMode="decimal"
            placeholder="—"
            disabled={disabled}
            aria-invalid={invalidScore}
          />
          <small>/20</small>
        </label>
        <label className="note-assessment-coefficient">
          <span className="sr-only">Coefficient</span>
          <input
            type="number"
            value={assessment.coefficient}
            onChange={(event) => onChange({ coefficient: event.target.value })}
            min="0.01"
            max="100"
            step="0.1"
            inputMode="decimal"
            disabled={disabled}
            aria-invalid={invalidCoefficient}
          />
          <small>coeff.</small>
        </label>
        <label className="note-resit-switch">
          <input
            type="checkbox"
            role="switch"
            checked={assessment.is_resit}
            onChange={(event) => onChange({ is_resit: event.target.checked })}
            disabled={disabled}
          />
          <span>Rattrapage</span>
        </label>
        <div className="note-assessment-nature">
          <NaturePill nature={nature} />
          {assessment.server?.source?.observed_at && (
            <small
              className="note-source-meta"
              title={`Observation PASS : ${formatDate(assessment.server.source.observed_at)}`}
            >
              PASS · {formatDate(assessment.server.source.observed_at, false)}
            </small>
          )}
          {sourceStatus === "unavailable" && (
            <small className="note-source-alert">
              <AlertTriangle size={11} /> Source indisponible
            </small>
          )}
        </div>
        <button
          className="icon-button note-assessment-remove"
          type="button"
          onClick={onRemove}
          disabled={disabled}
          title="Supprimer l’évaluation"
          aria-label={`Supprimer ${assessment.label || "l’évaluation"}`}
        >
          <X size={16} />
        </button>
      </div>
      {sourceStatus === "conflict" && (
        <ConflictPanel
          label="La note, son coefficient ou son statut a évolué depuis l’import."
          disabled={disabled}
          onResolve={onResolve}
        />
      )}
    </Fragment>
  );
}

function UeEditor({
  ue,
  expanded,
  disabled,
  onToggle,
  onChange,
  onAssessmentChange,
  onAddAssessment,
  onRemoveAssessment,
  onRemove,
  onResolveUe,
  onResolveAssessment,
}: {
  ue: NoteSimulationUeDraft;
  expanded: boolean;
  disabled: boolean;
  onToggle: (open: boolean) => void;
  onChange: (changes: Partial<NoteSimulationUeDraft>) => void;
  onAssessmentChange: (key: string, changes: Partial<NoteSimulationAssessmentDraft>) => void;
  onAddAssessment: () => void;
  onRemoveAssessment: (key: string) => void;
  onRemove: () => void;
  onResolveUe: (resolution: Resolution) => void;
  onResolveAssessment: (id: string, resolution: Resolution) => void;
}) {
  const projection = calculateNoteUeProjection(ue);
  const nature = ueNature(ue);
  const sourceStatus = ue.server?.source?.status;
  const identityMissing = !ue.ue_code.trim() && !ue.title.trim();
  const credits = numberOrNull(ue.credits_ects);
  const invalidCredits = credits !== null && (!Number.isFinite(credits) || credits <= 0 || credits > 60);
  return (
    <details
      className={`note-simulation-ue nature-${nature}${sourceStatus === "conflict" ? " has-conflict" : ""}`}
      open={expanded}
      onToggle={(event) => onToggle(event.currentTarget.open)}
    >
      <summary>
        <span className="note-ue-semester">{ue.semester ?? "—"}</span>
        <span className="note-ue-heading">
          <strong>{ue.title || ue.ue_code || "UE à compléter"}</strong>
          <small>
            {ue.ue_code || "Code libre"} ·{" "}
            {ue.credits_ects ? `${formatNumber(Number(ue.credits_ects))} ECTS` : "ECTS à renseigner"}
          </small>
        </span>
        <span className="note-ue-average">
          <small>Moyenne</small>
          <strong>
            {formatNumber(projection.average)}
            <i>/20</i>
          </strong>
        </span>
        <span className="note-ue-grade">
          <GradeBadge grade={projection.grade} />
          <small>
            {projection.usedResit && projection.grade === "E"
              ? "rattrapage validé"
              : projection.grade
                ? `${formatNumber(projection.gpaPoints)} GPA`
                : "en attente"}
          </small>
        </span>
        <span className="note-ue-progress">
          <strong>
            {projection.scoredCount}/{projection.assessmentCount}
          </strong>
          <small>notes</small>
        </span>
        <ChevronDown className="note-ue-chevron" size={18} />
      </summary>
      <div className="note-ue-body">
        <div className={`note-ue-metadata${identityMissing || invalidCredits ? " is-incomplete" : ""}`}>
          <label>
            <span>Semestre</span>
            <select
              value={ue.semester ?? ""}
              onChange={(event) => onChange({ semester: (event.target.value || null) as SimulationSemester | null })}
              disabled={disabled}
            >
              <option value="">Non défini</option>
              {SIMULATION_SEMESTERS.map((semester) => (
                <option key={semester}>{semester}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Code UE</span>
            <input
              value={ue.ue_code}
              onChange={(event) => onChange({ ue_code: event.target.value.toUpperCase() })}
              maxLength={32}
              placeholder="Ex. INF210"
              disabled={disabled}
              aria-invalid={identityMissing}
            />
          </label>
          <label className="note-ue-title-field">
            <span>Intitulé</span>
            <input
              value={ue.title}
              onChange={(event) => onChange({ title: event.target.value })}
              maxLength={200}
              placeholder="Intitulé de l’UE"
              disabled={disabled}
              aria-invalid={identityMissing}
            />
          </label>
          <label>
            <span>Crédits</span>
            <span className="note-input-suffix">
              <input
                type="number"
                value={ue.credits_ects}
                onChange={(event) => onChange({ credits_ects: event.target.value })}
                min="0.01"
                max="60"
                step="0.5"
                inputMode="decimal"
                placeholder="—"
                disabled={disabled}
                aria-invalid={invalidCredits}
              />
              <small>ECTS</small>
            </span>
          </label>
          <div className="note-ue-origin">
            <NaturePill nature={nature} />
            {ue.server?.source?.observed_at && (
              <small
                className="note-source-meta"
                title={`Observation COMPETENCES : ${formatDate(ue.server.source.observed_at)}`}
              >
                COMPETENCES · {formatDate(ue.server.source.observed_at, false)}
              </small>
            )}
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onRemove}
            disabled={disabled}
            title="Supprimer l’UE"
            aria-label={`Supprimer ${ue.title || ue.ue_code || "l’UE"}`}
          >
            <Trash2 size={16} />
          </button>
        </div>
        {sourceStatus === "conflict" && (
          <ConflictPanel
            label="Le semestre, l’intitulé ou les ECTS officiels ont évolué depuis l’import."
            disabled={disabled}
            onResolve={onResolveUe}
          />
        )}
        {sourceStatus === "unavailable" && (
          <div className="note-source-unavailable">
            <AlertTriangle size={15} />
            <span>Cette UE n’apparaît plus dans la source actuelle. Elle reste dans ta simulation.</span>
          </div>
        )}
        <div className="note-assessments">
          <div className="note-assessment-head" aria-hidden="true">
            <span>Évaluation</span>
            <span>Note</span>
            <span>Coefficient</span>
            <span>Statut</span>
            <span>Nature</span>
            <span />
          </div>
          <div className="note-assessment-list">
            {ue.assessments.length ? (
              ue.assessments.map((assessment) => (
                <AssessmentRow
                  key={assessment.clientKey}
                  assessment={assessment}
                  disabled={disabled}
                  onChange={(changes) => onAssessmentChange(assessment.clientKey, changes)}
                  onRemove={() => onRemoveAssessment(assessment.clientKey)}
                  onResolve={(resolution) => assessment.id && onResolveAssessment(assessment.id, resolution)}
                />
              ))
            ) : (
              <EmptyState
                icon={<BarChart3 size={19} />}
                title="Aucune évaluation"
                detail="Ajoute une note potentielle pour calculer la moyenne de cette UE."
              />
            )}
          </div>
          <footer>
            <button
              className="secondary-button"
              type="button"
              onClick={onAddAssessment}
              disabled={disabled || ue.assessments.length >= 60}
            >
              <Plus size={16} /> Ajouter une évaluation
            </button>
            <span>
              <Info size={14} /> Une note vide reste en attente.
            </span>
          </footer>
        </div>
      </div>
    </details>
  );
}

export function NoteSimulationsPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const accountId = session.data?.account?.id ?? "anonymous";
  const simulations = useNoteSimulations();
  const [activeId, setActiveId] = useState<string | null>(null);
  const scenario = useNoteSimulation(activeId);
  const [draft, setDraft] = useState<NoteSimulationDraft | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [semester, setSemester] = useState<"all" | SimulationSemester>("all");
  const [expandedUes, setExpandedUes] = useState<Set<string>>(new Set());
  const [creationOpen, setCreationOpen] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonId, setComparisonId] = useState("");
  const revision = useRef(0);
  const draftRef = useRef<NoteSimulationDraft | null>(null);
  const saveStateRef = useRef<SaveState>("saved");
  const savePendingRef = useRef(false);
  const scenarios = useMemo(() => simulations.data?.scenarios ?? [], [simulations.data?.scenarios]);

  const cacheScenario = (next: NoteSimulationScenario) => {
    queryClient.setQueryData(queryKeys.noteSimulation(accountId, next.id), next);
    queryClient.setQueryData<NoteSimulationList>(queryKeys.noteSimulations(accountId), (current) =>
      current
        ? {
            ...current,
            scenarios: (current.scenarios.some((item) => item.id === next.id)
              ? current.scenarios.map((item) => (item.id === next.id ? scenarioSummary(next) : item))
              : [scenarioSummary(next), ...current.scenarios]
            ).sort((left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()),
          }
        : current,
    );
  };

  useEffect(() => {
    if (!simulations.data) return;
    if (activeId && simulations.data.scenarios.some((item) => item.id === activeId)) return;
    setActiveId(simulations.data.scenarios[0]?.id ?? null);
  }, [activeId, simulations.data]);

  useEffect(() => {
    if (!scenario.data) {
      if (!activeId) setDraft(null);
      return;
    }
    const switching = draft?.id !== scenario.data.id;
    const serverAhead = draft && scenario.data.version > draft.version;
    if (switching || !draft || (serverAhead && saveState === "saved")) {
      const next = noteScenarioToDraft(scenario.data);
      revision.current = 0;
      setDraft(next);
      setSaveState("saved");
      setSemester("all");
      setExpandedUes(new Set(next.ues.map((ue) => ue.clientKey)));
    }
  }, [activeId, draft, saveState, scenario.data]);

  useEffect(() => {
    if (!comparisonOpen || (comparisonId && comparisonId !== activeId)) return;
    setComparisonId(scenarios.find((item) => item.id !== activeId)?.id ?? "");
  }, [activeId, comparisonId, comparisonOpen, scenarios]);

  const createMutation = useMutation({
    mutationFn: ({ name, importCurrent }: { name: string; importCurrent: boolean }) =>
      apiData(
        noteSimulationsScenarioCreate({
          body: { name, import_current: importCurrent },
          throwOnError: throwOnApiError,
        }),
      ),
    onSuccess: (created) => {
      const next = noteScenarioToDraft(created);
      cacheScenario(created);
      void queryClient.invalidateQueries({ queryKey: queryKeys.noteSimulations(accountId) });
      setCreationOpen(false);
      setActiveId(created.id);
      setDraft(next);
      setExpandedUes(new Set(next.ues.map((ue) => ue.clientKey)));
      setSaveState("saved");
      showToast(
        created.created_from === "academic" ? "Notes actuelles importées dans la simulation" : "Simulation créée",
      );
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const saveMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: ReturnType<typeof noteSimulationPayload>;
      localRevision: number;
      sentKeys: ReturnType<typeof noteSimulationSentKeys>;
    }) =>
      apiData(
        noteSimulationsScenarioSave({
          path: { scenario_id: id },
          body,
          throwOnError: throwOnApiError,
        }),
      ),
    onMutate: () => setSaveState("saving"),
    onSuccess: (saved, variables) => {
      cacheScenario(saved);
      if (revision.current === variables.localRevision) {
        const next = noteScenarioToDraft(saved);
        setDraft(next);
        setExpandedUes(new Set(next.ues.map((ue) => ue.clientKey)));
        setSaveState("saved");
      } else {
        setDraft((current) => (current ? mergeSavedNoteIds(current, saved, variables.sentKeys) : current));
        setSaveState("dirty");
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "simulation_version_conflict") {
        setSaveState("conflict");
        return;
      }
      setSaveState("error");
      showToast(error.message, "error");
    },
  });

  draftRef.current = draft;
  saveStateRef.current = saveState;
  savePendingRef.current = saveMutation.isPending;

  useEffect(() => {
    if (saveState === "saved") return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [saveState]);

  useEffect(
    () => () => {
      const current = draftRef.current;
      if (!current || saveStateRef.current !== "dirty" || savePendingRef.current || !noteDraftIsValid(current)) return;
      void apiData(
        noteSimulationsScenarioSave({
          path: { scenario_id: current.id },
          body: noteSimulationPayload(current),
          throwOnError: throwOnApiError,
        }),
      ).catch(() => undefined);
    },
    [],
  );

  const validDraft = Boolean(draft && noteDraftIsValid(draft));
  useEffect(() => {
    if (!draft || saveState !== "dirty" || !validDraft || saveMutation.isPending) return;
    const timer = window.setTimeout(() => {
      saveMutation.mutate({
        id: draft.id,
        body: noteSimulationPayload(draft),
        localRevision: revision.current,
        sentKeys: noteSimulationSentKeys(draft),
      });
    }, 700);
    return () => window.clearTimeout(timer);
  }, [draft, saveMutation, saveState, validDraft]);

  const actionMutation = useMutation({
    mutationFn: async ({
      action,
      payload,
    }: {
      action: "duplicate" | "reset" | "delete" | "rebase";
      payload?: { name?: string };
    }) => {
      if (!activeId || !draft) throw new Error("Simulation introuvable");
      if (action === "delete") {
        await apiData(
          noteSimulationsScenarioDelete({
            path: { scenario_id: activeId },
            query: { version: draft.version },
            throwOnError: throwOnApiError,
          }),
        );
        return { action, scenario: null };
      }
      const options = {
        path: { scenario_id: activeId },
        body: { version: draft.version, ...payload },
        throwOnError: throwOnApiError,
      };
      const next =
        action === "duplicate"
          ? await apiData(noteSimulationsScenarioDuplicate(options))
          : action === "rebase"
            ? await apiData(noteSimulationsScenarioRebase(options))
            : await apiData(noteSimulationsScenarioReset(options));
      return { action, scenario: next };
    },
    onSuccess: ({ action, scenario: next }) => {
      setConfirmation(null);
      if (action === "delete") {
        const replacement = scenarios.find((item) => item.id !== activeId)?.id ?? null;
        queryClient.removeQueries({ queryKey: queryKeys.noteSimulation(accountId, activeId ?? "none") });
        setActiveId(replacement);
        setDraft(null);
        void queryClient.invalidateQueries({ queryKey: queryKeys.noteSimulations(accountId) });
        showToast("Simulation supprimée");
        return;
      }
      if (!next) return;
      const nextDraft = noteScenarioToDraft(next);
      cacheScenario(next);
      void queryClient.invalidateQueries({ queryKey: queryKeys.noteSimulations(accountId) });
      if (action === "duplicate") setActiveId(next.id);
      setDraft(nextDraft);
      setExpandedUes(new Set(nextDraft.ues.map((ue) => ue.clientKey)));
      setSaveState("saved");
      showToast(
        action === "duplicate"
          ? "Simulation dupliquée"
          : action === "rebase"
            ? "Source officielle actualisée"
            : "Simulation réinitialisée",
      );
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "simulation_version_conflict") {
        setConfirmation(null);
        setSaveState("conflict");
        return;
      }
      showToast(error.message, "error");
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({ target, id, resolution }: { target: "ues" | "assessments"; id: string; resolution: Resolution }) => {
      if (!activeId || !draft) throw new Error("Simulation introuvable");
      const options = {
        path: { scenario_id: activeId },
        body: { version: draft.version, resolution },
        throwOnError: throwOnApiError,
      };
      return target === "ues"
        ? apiData(noteSimulationsResolveUeConflict({ ...options, path: { ...options.path, ue_id: id } }))
        : apiData(
            noteSimulationsResolveAssessmentConflict({
              ...options,
              path: { ...options.path, assessment_id: id },
            }),
          );
    },
    onSuccess: (next) => {
      cacheScenario(next);
      setDraft(noteScenarioToDraft(next));
      setSaveState("saved");
      showToast("Conflit résolu");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const preserveConflictMutation = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("Simulation introuvable");
      const created = await apiData(
        noteSimulationsScenarioCreate({
          body: { name: `${draft.name.slice(0, 64)} - copie locale`, import_current: false },
          throwOnError: throwOnApiError,
        }),
      );
      const body = noteSimulationPayload({ ...draft, id: created.id, version: created.version });
      body.ues = body.ues.map(({ id: _ueId, ...ue }) => ({
        ...ue,
        assessments: ue.assessments.map(({ id: _assessmentId, ...assessment }) => assessment),
      }));
      try {
        return await apiData(
          noteSimulationsScenarioSave({
            path: { scenario_id: created.id },
            body,
            throwOnError: throwOnApiError,
          }),
        );
      } catch (error) {
        await apiData(
          noteSimulationsScenarioDelete({
            path: { scenario_id: created.id },
            query: { version: created.version },
            throwOnError: throwOnApiError,
          }),
        ).catch(() => undefined);
        throw error;
      }
    },
    onSuccess: (created) => {
      queryClient.setQueryData(queryKeys.noteSimulation(accountId, created.id), created);
      void queryClient.invalidateQueries({ queryKey: queryKeys.noteSimulations(accountId) });
      setActiveId(created.id);
      setDraft(noteScenarioToDraft(created));
      setSaveState("saved");
      showToast("Modifications conservées dans une copie");
    },
    onError: (error) => showToast(error.message, "error"),
  });

  const reloadServerVersion = async () => {
    const refreshed = await scenario.refetch();
    if (!refreshed.data) return;
    revision.current = 0;
    setDraft(noteScenarioToDraft(refreshed.data));
    setSaveState("saved");
    showToast("Version serveur rechargée");
  };

  const updateDraft = (updater: (current: NoteSimulationDraft) => NoteSimulationDraft) => {
    revision.current += 1;
    setDraft((current) => (current ? updater(current) : current));
    setSaveState("dirty");
  };
  const updateUe = (key: string, changes: Partial<NoteSimulationUeDraft>) =>
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) => (ue.clientKey === key ? { ...ue, ...changes } : ue)),
    }));
  const updateAssessment = (ueKey: string, assessmentKey: string, changes: Partial<NoteSimulationAssessmentDraft>) =>
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) =>
        ue.clientKey === ueKey
          ? {
              ...ue,
              assessments: ue.assessments.map((assessment) =>
                assessment.clientKey === assessmentKey ? { ...assessment, ...changes } : assessment,
              ),
            }
          : ue,
      ),
    }));
  const removeAssessment = (ueKey: string, assessmentKey: string) =>
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) =>
        ue.clientKey === ueKey
          ? { ...ue, assessments: ue.assessments.filter((assessment) => assessment.clientKey !== assessmentKey) }
          : ue,
      ),
    }));
  const addAssessment = (ueKey: string) =>
    updateDraft((current) => ({
      ...current,
      ues: current.ues.map((ue) =>
        ue.clientKey === ueKey ? { ...ue, assessments: [...ue.assessments, createNoteAssessment()] } : ue,
      ),
    }));
  const removeUe = (ueKey: string) =>
    updateDraft((current) => ({ ...current, ues: current.ues.filter((ue) => ue.clientKey !== ueKey) }));
  const addUe = () => {
    const next = createNoteUe(semester === "all" ? null : semester);
    setExpandedUes((current) => new Set(current).add(next.clientKey));
    updateDraft((current) => ({ ...current, ues: [...current.ues, next] }));
  };

  const projection = useMemo(() => calculateNoteDraftProjection(draft?.ues ?? []), [draft?.ues]);
  const selectedProjection =
    semester === "all"
      ? projection
      : (projection.semesters.find((item) => item.semester === semester) ?? {
          average: null,
          gpa: null,
          creditsIncluded: 0,
          ueCount: 0,
          calculatedUeCount: 0,
          assessmentCount: 0,
          scoredCount: 0,
          pendingCount: 0,
        });
  const visibleUes = draft?.ues.filter((ue) => semester === "all" || ue.semester === semester) ?? [];
  const availableSemesters = useMemo(
    () => SIMULATION_SEMESTERS.filter((value) => draft?.ues.some((ue) => ue.semester === value)),
    [draft?.ues],
  );
  const currentSummary = scenarios.find((item) => item.id === activeId);
  const editorDisabled = saveState === "conflict";

  if (simulations.isPending)
    return (
      <div className="simulation-page-loading" aria-busy="true">
        <div className="skeleton simulation-tabs-skeleton" />
        <div className="skeleton simulation-summary-skeleton" />
        <div className="skeleton simulation-editor-skeleton" />
      </div>
    );
  if (simulations.isError)
    return (
      <section className="content-panel">
        <EmptyState
          icon={<AlertTriangle size={21} />}
          title="Simulations indisponibles"
          detail={simulations.error.message}
          action={
            <button className="secondary-button" type="button" onClick={() => simulations.refetch()}>
              <RefreshCw size={16} /> Réessayer
            </button>
          }
        />
      </section>
    );

  return (
    <div className="simulations-page note-simulations-page">
      <section className="simulation-privacy-band">
        <span>
          <ShieldCheck size={20} />
        </span>
        <div>
          <strong>Laboratoire de notes privé</strong>
          <p>Teste librement des résultats futurs sans toucher aux données officielles.</p>
        </div>
        <div>
          <i>Calcul instantané</i>
          <small>Coefficients puis ECTS</small>
        </div>
      </section>
      <section className="simulation-scenario-bar" aria-label="Scénarios de notes">
        <div className="simulation-tabs" role="tablist" aria-label="Choisir une simulation">
          {scenarios.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === activeId}
              className={item.id === activeId ? "active" : ""}
              onClick={() => setActiveId(item.id)}
              disabled={item.id !== activeId && saveState !== "saved"}
              title={item.id !== activeId && saveState !== "saved" ? "Attends la fin de l’enregistrement" : undefined}
            >
              <span>{item.name}</span>
              <small>
                {item.result.average === null ? "Moyenne —" : `${formatNumber(item.result.average)}/20`} ·{" "}
                {item.result.ue_count} UE
              </small>
              {item.rebase_available && <i aria-label="Source actualisée" title="Source actualisée" />}
            </button>
          ))}
        </div>
        <button
          className="icon-button simulation-add-scenario"
          type="button"
          onClick={() => setCreationOpen(true)}
          disabled={scenarios.length >= (simulations.data?.limit ?? 5) || saveState !== "saved"}
          aria-label="Créer une simulation"
          title={
            scenarios.length >= (simulations.data?.limit ?? 5)
              ? "Limite de cinq simulations de notes atteinte"
              : "Nouvelle simulation"
          }
        >
          <Plus size={19} />
        </button>
        <span className="simulation-limit">
          {scenarios.length}/{simulations.data?.limit ?? 5}
        </span>
      </section>

      {!scenarios.length ? (
        <section className="simulation-empty-panel">
          <EmptyState
            icon={<BarChart3 size={23} />}
            title="Aucune simulation de notes"
            detail="Importe tes résultats actuels ou construis un futur semestre à partir de zéro."
            action={
              <button className="primary-button" type="button" onClick={() => setCreationOpen(true)}>
                <Plus size={17} /> Créer ma première simulation
              </button>
            }
          />
        </section>
      ) : scenario.isPending || !draft || !currentSummary ? (
        <div className="simulation-page-loading" aria-busy="true">
          <div className="skeleton simulation-summary-skeleton" />
          <div className="skeleton simulation-editor-skeleton" />
        </div>
      ) : (
        <>
          {currentSummary.rebase_available && (
            <section className="simulation-rebase-banner">
              <History size={20} />
              <div>
                <strong>Tes notes officielles ont évolué</strong>
                <p>
                  Actualise la base du scénario. Tes hypothèses restent conservées et les divergences seront signalées.
                </p>
              </div>
              <button
                className="secondary-button"
                type="button"
                onClick={() => actionMutation.mutate({ action: "rebase" })}
                disabled={saveState !== "saved" || actionMutation.isPending}
              >
                {actionMutation.isPending ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}{" "}
                Actualiser la base
              </button>
            </section>
          )}
          {saveState === "conflict" && (
            <section className="simulation-version-banner">
              <CloudOff size={20} />
              <div>
                <strong>Une version plus récente existe</strong>
                <p>Tes changements locaux restent affichés sans écraser l’autre onglet.</p>
              </div>
              <div>
                <button className="secondary-button" type="button" onClick={reloadServerVersion}>
                  Recharger
                </button>
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => preserveConflictMutation.mutate()}
                  disabled={preserveConflictMutation.isPending}
                >
                  {preserveConflictMutation.isPending ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Copy size={16} />
                  )}{" "}
                  Conserver en copie
                </button>
              </div>
            </section>
          )}
          {saveState === "error" && (
            <section className="simulation-save-error-banner">
              <CloudOff size={20} />
              <div>
                <strong>L’enregistrement n’a pas abouti</strong>
                <p>Tes modifications sont toujours présentes dans cette page.</p>
              </div>
              <button className="secondary-button" type="button" onClick={() => setSaveState("dirty")}>
                <RefreshCw size={16} /> Réessayer
              </button>
            </section>
          )}

          <section className="simulation-workspace note-simulation-workspace">
            <header className="simulation-workspace-header">
              <div className="simulation-title-wrap">
                <label>
                  <span className="sr-only">Nom de la simulation</span>
                  <input
                    value={draft.name}
                    onChange={(event) => updateDraft((current) => ({ ...current, name: event.target.value }))}
                    maxLength={80}
                    disabled={editorDisabled}
                  />
                </label>
                <SaveIndicator state={saveState} valid={validDraft} />
                <small>
                  {currentSummary.source_captured_at
                    ? `Base PASS + COMPETENCES du ${formatDate(currentSummary.source_captured_at, false)}`
                    : "Scénario manuel"}{" "}
                  · modifié {relativeDate(currentSummary.updated_at)}
                </small>
              </div>
              <div className="simulation-workspace-actions">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setComparisonOpen(true)}
                  disabled={scenarios.length < 2 || saveState !== "saved"}
                >
                  <ArrowLeftRight size={16} /> Comparer
                </button>
                <details className="simulation-overflow">
                  <summary className="icon-button" aria-label="Actions sur la simulation" title="Plus d’actions">
                    <EllipsisVertical size={18} />
                  </summary>
                  <div>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        actionMutation.mutate({
                          action: "duplicate",
                          payload: { name: `${draft.name.slice(0, 68)} - copie` },
                        });
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <Copy size={16} /> Dupliquer
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        setConfirmation("reset");
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <RotateCcw size={16} /> Réinitialiser
                    </button>
                    <button
                      className="danger"
                      type="button"
                      onClick={(event) => {
                        event.currentTarget.closest("details")?.removeAttribute("open");
                        setConfirmation("delete");
                      }}
                      disabled={saveState !== "saved"}
                    >
                      <Trash2 size={16} /> Supprimer
                    </button>
                  </div>
                </details>
              </div>
            </header>
            <div className="simulation-summary-band note-simulation-summary">
              <div className="simulation-gpa-primary">
                <span>{semester === "all" ? "Moyenne générale simulée" : `Moyenne simulée · ${semester}`}</span>
                <strong>{formatNumber(selectedProjection.average)}</strong>
                <small>sur 20</small>
              </div>
              <div>
                <span>GPA potentiel</span>
                <strong>{formatNumber(selectedProjection.gpa)}</strong>
                <small>sur 4,00</small>
              </div>
              <div>
                <span>UE calculées</span>
                <strong>
                  {selectedProjection.calculatedUeCount}
                  <i>/{selectedProjection.ueCount}</i>
                </strong>
                <small>{formatNumber(selectedProjection.creditsIncluded)} ECTS pondérés</small>
              </div>
              <div>
                <span>Notes en attente</span>
                <strong>{selectedProjection.pendingCount}</strong>
                <small>
                  {semester === "all"
                    ? `${projection.completionRate} % renseigné`
                    : `${selectedProjection.scoredCount}/${selectedProjection.assessmentCount} notes`}
                </small>
              </div>
            </div>
            <div className="simulation-semester-toolbar">
              <div className="simulation-semester-tabs" role="tablist" aria-label="Filtrer par semestre">
                <button
                  type="button"
                  role="tab"
                  aria-selected={semester === "all"}
                  className={semester === "all" ? "active" : ""}
                  onClick={() => setSemester("all")}
                >
                  Tous
                </button>
                {availableSemesters.map((item) => (
                  <button
                    key={item}
                    type="button"
                    role="tab"
                    aria-selected={semester === item}
                    className={semester === item ? "active" : ""}
                    onClick={() => setSemester(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <span>
                {visibleUes.length} UE affichée{visibleUes.length === 1 ? "" : "s"}
              </span>
            </div>
            <div className="note-ue-list">
              {visibleUes.length ? (
                visibleUes.map((ue) => (
                  <UeEditor
                    key={ue.clientKey}
                    ue={ue}
                    expanded={expandedUes.has(ue.clientKey)}
                    disabled={editorDisabled}
                    onToggle={(open) =>
                      setExpandedUes((current) => {
                        const next = new Set(current);
                        if (open) next.add(ue.clientKey);
                        else next.delete(ue.clientKey);
                        return next;
                      })
                    }
                    onChange={(changes) => updateUe(ue.clientKey, changes)}
                    onAssessmentChange={(key, changes) => updateAssessment(ue.clientKey, key, changes)}
                    onAddAssessment={() => addAssessment(ue.clientKey)}
                    onRemoveAssessment={(key) => removeAssessment(ue.clientKey, key)}
                    onRemove={() => removeUe(ue.clientKey)}
                    onResolveUe={(resolution) =>
                      ue.id && resolveMutation.mutate({ target: "ues", id: ue.id, resolution })
                    }
                    onResolveAssessment={(id, resolution) =>
                      resolveMutation.mutate({ target: "assessments", id, resolution })
                    }
                  />
                ))
              ) : (
                <EmptyState
                  icon={<BarChart3 size={21} />}
                  title={semester === "all" ? "Scénario vide" : `Aucune UE en ${semester}`}
                  detail={
                    semester === "all"
                      ? "Ajoute une UE pour commencer ta projection."
                      : "Ajoute une UE, elle sera directement placée dans ce semestre."
                  }
                />
              )}
            </div>
            <footer className="note-simulation-footer">
              <button
                className="secondary-button"
                type="button"
                onClick={addUe}
                disabled={editorDisabled || draft.ues.length >= 120}
              >
                <Plus size={17} /> Ajouter une UE{semester === "all" ? "" : ` en ${semester}`}
              </button>
              <span>
                <Info size={14} /> Moyenne d’UE par coefficients, moyenne générale par ECTS.
              </span>
            </footer>
            <details className="simulation-formula">
              <summary>
                <span>
                  <Info size={16} /> Méthode de calcul
                </span>
                <ChevronDown size={17} />
              </summary>
              <div className="note-formula-content">
                <p>
                  <strong>Moyenne UE = somme des notes × coefficients ÷ somme des coefficients.</strong> La moyenne
                  générale pondère ensuite chaque moyenne d’UE par ses ECTS. Une note vide est exclue, jamais remplacée
                  par zéro.
                </p>
                <div>
                  <span>
                    <strong>Rattrapage</strong>
                    <small>
                      La dernière note de rattrapage saisie remplace la moyenne normale. Si elle valide l’UE, le grade
                      potentiel devient E.
                    </small>
                  </span>
                  <span>
                    <strong>GPA dérivé</strong>
                    <small>
                      Calculé sur 4 à partir du grade potentiel de chaque UE. Il reste indicatif et n’alimente jamais le
                      classement.
                    </small>
                  </span>
                </div>
                <small>
                  Règle IMTégrale {currentSummary.formula_version} · échelle 0–20 puis 0–4 · arrondi au centième,
                  demi-supérieur · simulation non officielle.
                </small>
              </div>
            </details>
          </section>
        </>
      )}

      <CreationModal
        open={creationOpen}
        sourceUeCount={simulations.data?.source.ue_count ?? 0}
        sourceAssessmentCount={simulations.data?.source.assessment_count ?? 0}
        pending={createMutation.isPending}
        onClose={() => setCreationOpen(false)}
        onCreate={(name, importCurrent) => createMutation.mutate({ name, importCurrent })}
      />
      <ConfirmationModal
        action={confirmation}
        name={draft?.name ?? ""}
        pending={actionMutation.isPending}
        resetDescription="Les valeurs importées retrouveront leur état initial et les ajouts manuels disparaîtront."
        deleteBody="Cette action ne touche ni tes notes officielles ni tes autres simulations."
        resetBody="Le scénario conserve sa base académique importée."
        onClose={() => setConfirmation(null)}
        onConfirm={() => confirmation && actionMutation.mutate({ action: confirmation })}
      />
      {currentSummary && (
        <ComparisonModal
          open={comparisonOpen}
          accountId={accountId}
          left={currentSummary}
          scenarios={scenarios}
          rightId={comparisonId}
          setRightId={setComparisonId}
          onClose={() => setComparisonOpen(false)}
        />
      )}
    </div>
  );
}
