from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.learning.schemas import (
    CatalogNodeKind,
    Difficulty,
    LearningSection,
    ReaderVisibility,
    ReleaseMode,
    ReviewStatus,
)

Role = Literal["owner", "editor", "viewer"]
AuthMethod = Literal["imt", "token", "passkey"]
SyncStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
AcademicSemester = Literal["S5", "S6", "S7", "S8", "S9", "S10"]
Grade = Literal["A", "B", "C", "D", "E", "FX", "F"]
Campus = Literal["rennes", "brest", "nantes", "other", "unknown"]
Cohort = Literal["1a", "2a", "3a", "higher", "atypical", "unknown"]
Freshness = Literal["current", "recommended", "stale"]
LearningContentKind = Literal["concept", "lesson", "exercise", "pc_td", "past_exam"]
LearningSelfAssessment = Literal[1, 2, 3, 4, 5]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorDetail(ApiModel):
    code: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApiErrorEnvelope(ApiModel):
    detail: ApiErrorDetail | str


class JsonObjectResponse(RootModel[dict[str, Any]]):
    pass


class JsonObjectListResponse(RootModel[list[dict[str, Any]]]):
    pass


class OkResponse(ApiModel):
    ok: Literal[True]


class LearningSessionResponse(ApiModel):
    available: bool
    audience_label: str | None
    level_label: str | None
    reverify_required: bool
    catalog_version: str | None


class SessionAccountResponse(ApiModel):
    id: str
    display_name: str
    imt_username: str | None


class UnauthenticatedSessionResponse(ApiModel):
    authenticated: Literal[False]


class AuthenticatedSessionResponse(ApiModel):
    authenticated: Literal[True]
    role: Role
    auth_method: AuthMethod
    needs_security_setup: bool
    needs_sync_setup: bool
    account: SessionAccountResponse
    learning: LearningSessionResponse


SessionResponse: TypeAlias = UnauthenticatedSessionResponse | AuthenticatedSessionResponse


class WebAuthnOptionsResponse(ApiModel):
    challenge_id: str
    publicKey: dict[str, Any]


class PasskeyResponse(ApiModel):
    id: str
    name: str
    device_type: Literal["single_device", "multi_device"] | None
    backed_up: bool
    transports: list[str]
    created_at: datetime
    last_used_at: datetime | None


class ShareTokenResponse(ApiModel):
    id: str
    name: str
    prefix: str
    role: Role
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ShareTokenCreatedResponse(ShareTokenResponse):
    token: str


class ServiceSessionResponse(ApiModel):
    state: Literal["active", "reauth_required", "owner_managed"]
    reauth_required: bool
    beta: Literal[True]
    retention_days: int
    established_at: datetime | None
    expires_at: datetime | None
    last_used_at: datetime | None
    pass_last_success_at: datetime | None
    hub_state: Literal["unknown", "ready", "degraded"]
    hub_last_attempt_at: datetime | None
    hub_last_success_at: datetime | None


class PassCircuitResponse(ApiModel):
    state: Literal["closed", "open", "half_open"]
    reason: str | None
    next_probe_at: datetime | None


class PassQuotaWindowResponse(ApiModel):
    used: int
    limit: int
    remaining: int


class PassQuotaResponse(ApiModel):
    hour: PassQuotaWindowResponse
    day: PassQuotaWindowResponse
    available_at: datetime
    retry_after_seconds: int


class PassProfileStatusResponse(ApiModel):
    refreshed_at: datetime | None
    refresh_due: bool


class PassAccessResponse(ApiModel):
    state: Literal["available", "busy", "resting", "circuit_open"]
    available: bool
    available_at: datetime
    retry_after_seconds: int
    circuit: PassCircuitResponse
    quota: PassQuotaResponse | None = None
    profile: PassProfileStatusResponse | None = None
    service_session: ServiceSessionResponse | None = None


class SyncRequestSummaryResponse(ApiModel):
    request_id: str
    status: SyncStatus
    actor: str
    accepted_at: datetime
    completed_at: datetime | None
    error_code: str | None


class ManualSyncResponse(ApiModel):
    state: Literal[
        "available",
        "cooldown",
        "in_progress",
        "pass_unavailable",
        "reauth_required",
    ]
    can_start: bool
    cooldown_seconds: int
    retry_after_seconds: int
    cooldown_until: datetime | None
    active_until: datetime | None
    server_time: datetime
    last_request: SyncRequestSummaryResponse | None
    pass_access: PassAccessResponse


class SyncStartResponse(ApiModel):
    ok: Literal[True]
    request_id: str
    status: SyncStatus
    idempotent_replay: bool
    accepted_at: datetime
    cooldown_until: datetime
    retry_after_seconds: int
    server_time: datetime
    error_code: str | None


class AccountSettingsResponse(ApiModel):
    display_name: str
    imt_username: str | None
    timezone: str
    campus: Literal["rennes", "brest", "nantes", "other", "unknown"]
    campus_source: str
    profile_refreshed_at: datetime | None
    program: str
    promotion_year: int | None
    academic_source: str
    academic_verified_at: datetime | None
    official_first_name: str | None
    official_last_name: str | None
    official_name: str | None
    official_identity_at: datetime | None


class TelegramSettingsResponse(ApiModel):
    configured: bool
    enabled: bool
    last_test_at: datetime | None
    last_test_status: Literal["pending", "success", "failed"] | None


class BusinessHoursResponse(ApiModel):
    weekdays: Literal["monday-friday"]
    start: str
    end: str
    timezone: str


class SyncSettingsResponse(ApiModel):
    enabled: bool
    interval_hours: Literal[2, 4, 6, 8, 12, 24]
    adaptive: bool
    current_interval_hours: Literal[2, 4, 6, 8, 12, 24]
    no_change_streak: int
    consented_at: datetime | None
    paused_reason: Literal["reauth_required"] | None
    paused_at: datetime | None
    next_eligible_at: datetime | None
    allowed_intervals: list[Literal[2, 4, 6, 8, 12, 24]]
    business_hours: BusinessHoursResponse
    pass_access: PassAccessResponse | None
    service_session: ServiceSessionResponse | None


class AccessSettingsResponse(ApiModel):
    role: Role
    auth_method: AuthMethod
    security_setup_completed: bool
    sync_setup_completed: bool
    passkey_count: int


class SettingsResponse(ApiModel):
    account: AccountSettingsResponse
    telegram: TelegramSettingsResponse
    sync: SyncSettingsResponse
    access: AccessSettingsResponse


class PassReconnectResponse(ApiModel):
    ok: Literal[True]
    service_session: ServiceSessionResponse


class TelegramTestResponse(ApiModel):
    ok: Literal[True]
    sent_at: datetime


class NoteResponse(ApiModel):
    id: str
    source: Literal["pass", "manual"]
    ue_code: str
    label: str
    score: float
    coefficient: float
    is_resit: bool
    has_override: bool
    editable: bool
    detected_at: datetime
    updated_at: datetime


class UeResponse(ApiModel):
    code: str
    title: str
    year: str
    semester: AcademicSemester | None
    official_code: str | None
    credits_ects: float | None
    earned_credits_ects: float | None
    metadata_source: Literal["manual", "competences"]
    metadata_refreshed_at: datetime | None
    average: float | None
    grade: Grade | None
    grade_description: str | None
    grade_source: Literal["competences", "pass_calculated", "manual_calculated"]
    gpa: float | None
    validated: bool
    used_resit: bool
    note_count: int


class EventResponse(ApiModel):
    id: int
    kind: str
    payload: dict[str, Any]
    actor: str
    created_at: datetime


class DashboardAccountResponse(ApiModel):
    id: str
    display_name: str
    imt_username: str | None
    last_sync_at: datetime | None
    last_sync_status: Literal["never", "running", "success", "error"]
    last_sync_error: str | None
    manual_sync: ManualSyncResponse | None
    telegram_enabled: bool


class AcademicSummaryResponse(ApiModel):
    average: float | None
    average_credits: float
    gpa: float | None
    gpa_credits: float
    validated_credits: float
    note_count: int
    ue_count: int
    missing_ects_count: int


class AcademicPeriodResponse(ApiModel):
    average: float | None
    average_credits: float
    gpa: float | None
    gpa_credits: float
    validated_credits: float
    ue_count: int


class AcademicYearResponse(AcademicPeriodResponse):
    year: str
    label: str


class AcademicSemesterResponse(AcademicPeriodResponse):
    semester: AcademicSemester
    label: AcademicSemester


class GradeDistributionResponse(ApiModel):
    grade: Grade
    count: int


class GradeScaleResponse(ApiModel):
    grade: Grade
    description: str
    gpa: float


class DashboardResponse(ApiModel):
    generated_at: datetime
    latest_event_id: int
    account: DashboardAccountResponse
    summary: AcademicSummaryResponse
    years: list[AcademicYearResponse]
    semesters: list[AcademicSemesterResponse]
    ues: list[UeResponse]
    notes: list[NoteResponse]
    grade_distribution: list[GradeDistributionResponse]
    grade_scale: list[GradeScaleResponse]
    events: list[EventResponse]


class CalendarStatusResponse(ApiModel):
    configured: bool
    refresh_interval_minutes: Literal[60]
    account_hint: str | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    next_refresh_at: datetime | None
    last_status: Literal["success", "error", "pending"] | None
    last_error_code: str | None
    event_count: int
    fip_training_available: bool
    promotion_year: int | None


class CalendarEventResponse(ApiModel):
    id: str
    title: str
    location: str | None
    start: str
    end: str
    all_day: bool


class FipTrainingPeriodResponse(ApiModel):
    kind: Literal["school", "company"]
    start: str
    end: str
    weeks: int
    campus: Literal["Rennes", "Brest"] | None


class FipTrainingMilestoneResponse(ApiModel):
    kind: Literal["international_project", "academic_mobility"]
    title: str
    start: str
    end: str
    detail: str


class FipTrainingSemesterResponse(ApiModel):
    semester: AcademicSemester
    start: str
    end: str


class FipTrainingTotalsResponse(ApiModel):
    school_weeks: int
    company_weeks: int


class FipTrainingPromotionResponse(ApiModel):
    promotion_year: int
    level: Literal["A1", "A2", "A3"]
    semesters: list[FipTrainingSemesterResponse]
    totals: FipTrainingTotalsResponse
    periods: list[FipTrainingPeriodResponse]
    milestones: list[FipTrainingMilestoneResponse]


class FipTrainingSourceResponse(ApiModel):
    label: str
    version_date: str


class FipTrainingCalendarResponse(ApiModel):
    academic_year: str
    title: str
    speciality: str
    source: FipTrainingSourceResponse
    promotions: list[FipTrainingPromotionResponse]
    default_promotion_year: int | None
    campus_note: str


class LeaderboardProfileResponse(ApiModel):
    official_first_name: str | None
    official_last_name: str | None
    official_name: str | None
    official_identity_at: datetime | None
    campus: Campus
    campus_source: str
    campus_confirmed_at: datetime | None
    detected_campus: Campus
    detected_campus_at: datetime | None
    cohort: Cohort
    cohort_source: str
    cohort_confirmed_at: datetime | None
    program: str
    promotion_year: int | None
    academic_source: str
    academic_verified_at: datetime | None
    segment: str | None
    classification_review_required: bool
    joined_at: datetime | None
    ranking_visible_at: datetime | None
    withdraw_available_at: datetime | None
    left_at: datetime | None
    rejoin_after: datetime | None
    verification_status: Literal["standard", "review", "suspended"]
    freshness: Freshness
    verified_at: datetime | None


class LeaderboardScoreResponse(ApiModel):
    average: float | None
    gpa: float | None
    credits: float
    ue_count: int
    note_count: int
    missing_ects_count: int


class LeaderboardEligibilityResponse(ApiModel):
    eligible: bool
    missing: list[Literal["identity", "campus", "promotion", "pass_notes", "ects"]]
    score: LeaderboardScoreResponse


class LeaderboardPublicationResponse(ApiModel):
    wait_complete: bool
    score_ready: bool


class LeaderboardRulesResponse(ApiModel):
    version: str
    updated_at: str
    wait_hours: int
    withdrawal_lock_hours: int
    rejoin_cooldown_hours: int
    source: str
    weighting: str
    segment: str
    excluded: list[str]
    ties: str
    freshness: str
    public_fields: list[str]


class LeaderboardEntryResponse(ApiModel):
    rank: int | None
    official_name: str
    score: float
    verified_at: datetime | None
    freshness: Freshness
    is_self: bool


class LeaderboardBoardResponse(ApiModel):
    metric: Literal["gpa", "average"]
    campus_filter: Literal["all", "rennes", "brest", "nantes", "other"]
    cohort_filter: Literal["official"]
    segment: str | None
    calculated_at: datetime
    participant_count: int
    entries: list[LeaderboardEntryResponse]


class LeaderboardResponse(ApiModel):
    state: Literal["not_joined", "pending", "active", "suspended"]
    profile: LeaderboardProfileResponse
    eligibility: LeaderboardEligibilityResponse
    can_withdraw: bool
    can_delete_data: bool
    consent_version: str
    publication: LeaderboardPublicationResponse
    rules: LeaderboardRulesResponse
    board: LeaderboardBoardResponse | None


class SimulationFormulaResponse(ApiModel):
    version: str
    label: str
    scale: str
    rounding: str
    scope: str
    expression: str
    official: Literal[False]


class SimulationWarningResponse(ApiModel):
    code: str
    count: int
    message: str


class SimulationSemesterResultResponse(ApiModel):
    semester: AcademicSemester
    gpa: float | None
    credits_included: float
    ue_count: int


class SimulationResultResponse(ApiModel):
    status: Literal["empty", "partial", "ready"]
    gpa: float | None
    credits_entered: float
    credits_included: float
    ue_count: int
    graded_count: int
    pending_count: int
    missing_ects_count: int
    completion_rate: int
    semesters: list[SimulationSemesterResultResponse]
    warnings: list[SimulationWarningResponse]
    formula: SimulationFormulaResponse


class SimulationSourceSummaryResponse(ApiModel):
    revision: str
    captured_at: datetime
    ue_count: int
    graded_count: int


class SimulationEntrySourceResponse(ApiModel):
    ue_code: str | None
    status: Literal["current", "conflict", "unavailable"]
    grade_source: Literal["competences", "pass_calculated"] | None
    observed_at: datetime | None


class SimulationEntryBaselineResponse(ApiModel):
    semester: AcademicSemester | None
    ue_code: str | None
    title: str | None
    credits_ects: float | None
    grade: Grade | None


class SimulationEntryResponse(ApiModel):
    id: str
    lineage_key: str
    semester: AcademicSemester | None
    ue_code: str | None
    title: str
    credits_ects: float | None
    grade: Grade | None
    gpa_points: float | None
    status: Literal["pending", "validated", "not_validated"]
    nature: Literal["imported", "modified", "simulated"]
    source: SimulationEntrySourceResponse | None
    baseline: SimulationEntryBaselineResponse | None
    created_at: datetime
    updated_at: datetime


class SimulationScenarioSummaryResponse(ApiModel):
    id: str
    name: str
    created_from: Literal["blank", "academic"]
    formula_version: str
    version: int
    source_revision: str | None
    source_captured_at: datetime | None
    rebase_available: bool
    created_at: datetime
    updated_at: datetime
    result: SimulationResultResponse


class SimulationScenarioResponse(SimulationScenarioSummaryResponse):
    entries: list[SimulationEntryResponse]


class SimulationListResponse(ApiModel):
    limit: int
    source: SimulationSourceSummaryResponse
    scenarios: list[SimulationScenarioSummaryResponse]


class SimulationDifferenceResponse(ApiModel):
    lineage_key: str
    kind: Literal["changed", "left_only", "right_only"]
    left: SimulationEntryResponse | None
    right: SimulationEntryResponse | None
    fields: list[Literal["presence", "semester", "ue", "credits_ects", "grade"]]


class SimulationComparisonResponse(ApiModel):
    left: SimulationScenarioSummaryResponse
    right: SimulationScenarioSummaryResponse
    gpa_delta: float | None
    differences: list[SimulationDifferenceResponse]
    formula: SimulationFormulaResponse


class NoteSimulationFormulaResponse(ApiModel):
    version: str
    label: str
    scale: str
    rounding: str
    scope: str
    ue_expression: str
    average_expression: str
    gpa_expression: str
    official: Literal[False]


class NoteSimulationAssessmentSourceResponse(ApiModel):
    note_key: str | None
    status: Literal["current", "conflict", "unavailable"]
    observed_at: datetime | None


class NoteSimulationAssessmentBaselineResponse(ApiModel):
    label: str | None
    score: float | None
    coefficient: float | None
    is_resit: bool | None


class NoteSimulationAssessmentResponse(ApiModel):
    id: str
    lineage_key: str
    label: str
    score: float | None
    coefficient: float
    is_resit: bool
    nature: Literal["imported", "modified", "simulated"]
    source: NoteSimulationAssessmentSourceResponse | None
    baseline: NoteSimulationAssessmentBaselineResponse | None
    created_at: datetime
    updated_at: datetime


class NoteSimulationUeProjectionResponse(ApiModel):
    average: float | None
    grade: Grade | None
    gpa_points: float | None
    used_resit: bool
    coefficient_total: float
    assessment_count: int
    scored_count: int
    pending_count: int


class NoteSimulationUeSourceResponse(ApiModel):
    ue_code: str | None
    status: Literal["current", "conflict", "unavailable"]
    observed_at: datetime | None


class NoteSimulationUeBaselineResponse(ApiModel):
    semester: AcademicSemester | None
    ue_code: str | None
    title: str | None
    credits_ects: float | None


class NoteSimulationUeResponse(ApiModel):
    id: str
    lineage_key: str
    semester: AcademicSemester | None
    ue_code: str | None
    title: str
    credits_ects: float | None
    nature: Literal["imported", "modified", "simulated"]
    projection: NoteSimulationUeProjectionResponse
    source: NoteSimulationUeSourceResponse | None
    baseline: NoteSimulationUeBaselineResponse | None
    assessments: list[NoteSimulationAssessmentResponse]
    created_at: datetime
    updated_at: datetime


class NoteSimulationSemesterResultResponse(ApiModel):
    semester: AcademicSemester
    average: float | None
    gpa: float | None
    credits_included: float
    ue_count: int
    calculated_ue_count: int
    assessment_count: int
    scored_count: int
    pending_count: int


class NoteSimulationResultResponse(ApiModel):
    status: Literal["empty", "partial", "ready"]
    average: float | None
    gpa: float | None
    credits_entered: float
    credits_included: float
    ue_count: int
    calculated_ue_count: int
    assessment_count: int
    scored_count: int
    pending_count: int
    missing_ects_count: int
    completion_rate: int
    semesters: list[NoteSimulationSemesterResultResponse]
    warnings: list[SimulationWarningResponse]
    formula: NoteSimulationFormulaResponse


class NoteSimulationSourceSummaryResponse(ApiModel):
    revision: str
    captured_at: datetime
    ue_count: int
    assessment_count: int
    scored_count: int


class NoteSimulationScenarioSummaryResponse(ApiModel):
    id: str
    name: str
    created_from: Literal["blank", "academic"]
    formula_version: str
    version: int
    source_revision: str | None
    source_captured_at: datetime | None
    rebase_available: bool
    created_at: datetime
    updated_at: datetime
    result: NoteSimulationResultResponse


class NoteSimulationScenarioResponse(NoteSimulationScenarioSummaryResponse):
    ues: list[NoteSimulationUeResponse]


class NoteSimulationListResponse(ApiModel):
    limit: int
    source: NoteSimulationSourceSummaryResponse
    scenarios: list[NoteSimulationScenarioSummaryResponse]


class NoteSimulationDifferenceResponse(ApiModel):
    lineage_key: str
    kind: Literal["changed", "left_only", "right_only"]
    left: NoteSimulationUeResponse | None
    right: NoteSimulationUeResponse | None
    fields: list[Literal["presence", "semester", "ue", "credits_ects", "assessments"]]


class NoteSimulationComparisonResponse(ApiModel):
    left: NoteSimulationScenarioSummaryResponse
    right: NoteSimulationScenarioSummaryResponse
    average_delta: float | None
    gpa_delta: float | None
    differences: list[NoteSimulationDifferenceResponse]
    formula: NoteSimulationFormulaResponse


class LearningAccessResponse(ApiModel):
    available: Literal[True]
    audience: str
    audience_label: str
    level_label: str
    reverify_required: Literal[False]
    catalog_version: str
    release_id: str


class LearningCatalogNodeResponse(ApiModel):
    id: str
    kind: CatalogNodeKind
    title: str
    code: str | None
    description: str | None
    parent_id: str | None
    content_id: str | None
    source_id: str | None
    prerequisite_ids: list[str]
    difficulty: Difficulty | None
    estimated_minutes: int | None
    section: LearningSection | None
    reader_visibility: ReaderVisibility
    document_type: Literal["pdf", "image", "download"] | None = None
    page_count: int | None = None
    source_serving_allowed: bool = False
    download_allowed: bool = False
    asset_url: str | None = None
    download_url: str | None = None
    review_status: ReviewStatus
    revision: str
    position: int


class LearningCatalogResponse(ApiModel):
    schema_version: Literal[1, 2, 3]
    release_mode: ReleaseMode
    release_id: str
    catalog_version: str
    audience: str
    nodes: list[LearningCatalogNodeResponse]


class LearningCatalogNodeEnvelopeResponse(ApiModel):
    release_id: str
    node: LearningCatalogNodeResponse


class LearningContentFrontmatterResponse(ApiModel):
    catalog_node_id: str
    title: str
    review_status: ReviewStatus
    revision: str
    prerequisite_ids: list[str]
    difficulty: Difficulty | None
    estimated_minutes: int | None


class LearningTextInlineResponse(ApiModel):
    type: Literal["text"]
    text: str
    marks: list[Literal["emphasis", "strong", "code"]]


class LearningMathInlineResponse(ApiModel):
    type: Literal["math"]
    latex: str


class LearningSourceReferenceInlineResponse(ApiModel):
    type: Literal["source_ref"]
    id: str
    source_id: str
    page: int
    end_page: int | None
    label: str | None


class LearningConceptReferenceInlineResponse(ApiModel):
    type: Literal["concept_ref"]
    concept_id: str
    label: str | None


class LearningExerciseReferenceInlineResponse(ApiModel):
    type: Literal["exercise_ref"]
    exercise_id: str
    label: str | None


class LearningLineBreakInlineResponse(ApiModel):
    type: Literal["line_break"]


LearningInlineResponse: TypeAlias = Annotated[
    LearningTextInlineResponse
    | LearningMathInlineResponse
    | LearningSourceReferenceInlineResponse
    | LearningConceptReferenceInlineResponse
    | LearningExerciseReferenceInlineResponse
    | LearningLineBreakInlineResponse,
    Field(discriminator="type"),
]


class LearningHeadingBlockResponse(ApiModel):
    type: Literal["heading"]
    id: str
    level: Literal[2, 3, 4, 5, 6]
    inlines: list[LearningInlineResponse]


class LearningParagraphBlockResponse(ApiModel):
    type: Literal["paragraph"]
    inlines: list[LearningInlineResponse]


class LearningListItemResponse(ApiModel):
    inlines: list[LearningInlineResponse]


class LearningListBlockResponse(ApiModel):
    type: Literal["list"]
    ordered: bool
    start: int | None
    items: list[LearningListItemResponse]


class LearningQuoteBlockResponse(ApiModel):
    type: Literal["quote"]
    inlines: list[LearningInlineResponse]


class LearningCodeBlockResponse(ApiModel):
    type: Literal["code"]
    code: str
    language: str | None


class LearningMathBlockResponse(ApiModel):
    type: Literal["math"]
    latex: str


class LearningImageBlockResponse(ApiModel):
    type: Literal["image"]
    asset_id: str
    alt_text: str
    caption: str | None


class LearningDirectiveBlockResponse(ApiModel):
    type: Literal["directive"]
    id: str
    name: Literal["note", "warning", "definition", "hint", "solution"]
    title: str | None
    inlines: list[LearningInlineResponse]


class LearningThematicBreakBlockResponse(ApiModel):
    type: Literal["thematic_break"]


LearningContentBlockResponse: TypeAlias = Annotated[
    LearningHeadingBlockResponse
    | LearningParagraphBlockResponse
    | LearningListBlockResponse
    | LearningQuoteBlockResponse
    | LearningCodeBlockResponse
    | LearningMathBlockResponse
    | LearningImageBlockResponse
    | LearningDirectiveBlockResponse
    | LearningThematicBreakBlockResponse,
    Field(discriminator="type"),
]


class LearningContentResponse(ApiModel):
    release_id: str
    id: str
    kind: LearningContentKind
    frontmatter: LearningContentFrontmatterResponse
    blocks: list[LearningContentBlockResponse]


class LearningSourcePageResponse(ApiModel):
    page: int
    label: str | None


class LearningSourceResponse(ApiModel):
    release_id: str
    id: str
    title: str
    asset_id: str | None
    revision: str
    pages: list[LearningSourcePageResponse]
    kind: Literal["image", "pdf", "download"] | None
    mime_type: str | None
    filename: str | None
    page_count: int
    source_serving_allowed: bool
    download_allowed: bool
    rights_label: str
    asset_url: str | None
    download_url: str | None


class LearningSourceReferenceResponse(ApiModel):
    release_id: str
    id: str
    content_id: str
    source_id: str
    source_title: str
    page: int
    end_page: int | None
    label: str | None
    source_url: str
    source_serving_allowed: bool
    download_allowed: bool
    asset_url: str | None
    download_url: str | None


class LearningSearchResultResponse(ApiModel):
    entity_id: str
    catalog_node_id: str
    entity_type: CatalogNodeKind
    title: str
    excerpt: str
    ue_id: str | None
    module_id: str | None
    semester: str | None
    difficulty: Difficulty | None
    estimated_minutes: int | None


class LearningSearchResponse(ApiModel):
    release_id: str
    items: list[LearningSearchResultResponse]
    has_more: bool
    next_offset: None


class LearningProgressItemResponse(ApiModel):
    content_id: str
    last_section_id: str | None
    last_page: int | None
    completed: bool
    exercise_viewed: bool
    opened_hint_ids: list[str]
    self_assessment: LearningSelfAssessment | None
    favorite: bool
    created_at: datetime
    updated_at: datetime


class LearningProgressSummaryResponse(ApiModel):
    started_count: int
    completed_lessons: int
    viewed_exercises: int
    favorite_count: int


class LearningProgressResponse(ApiModel):
    catalog_version: str
    items: list[LearningProgressItemResponse]
    summary: LearningProgressSummaryResponse


class LearningProgressResetCountsResponse(ApiModel):
    progress: int
    attempts: int


class LearningProgressResetResponse(ApiModel):
    deleted: LearningProgressResetCountsResponse


class LearningAttemptResponse(ApiModel):
    id: str
    exercise_id: str
    attempt_kind: Literal["viewed", "hint_opened", "self_assessed", "completed"]
    hint_id: str | None
    self_assessment: LearningSelfAssessment | None
    attempted_at: datetime


class LearningAttemptsResponse(ApiModel):
    items: list[LearningAttemptResponse]


class AdminUnauthenticatedSessionResponse(ApiModel):
    authenticated: Literal[False]


class AdminAuthenticatedSessionResponse(ApiModel):
    authenticated: Literal[True]
    username: str
    must_change_password: bool
    mfa_configured: bool
    mfa_verified: bool
    mfa_verified_at: datetime | None
    step_up_expires_at: datetime | None
    expires_at: datetime


AdminSessionResponse: TypeAlias = AdminUnauthenticatedSessionResponse | AdminAuthenticatedSessionResponse


class AdminTokenResponse(ApiModel):
    id: str
    name: str
    prefix: str
    role: Role
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class AdminLeaderboardResponse(ApiModel):
    state: Literal["not_joined", "pending", "active", "suspended"]
    official_first_name: str | None
    official_last_name: str | None
    official_identity_at: datetime | None
    has_leaderboard_data: bool
    campus: Campus
    detected_campus: Campus
    cohort: Cohort
    program: str
    promotion_year: int | None
    academic_source: str
    academic_verified_at: datetime | None
    profile_refreshed_at: datetime | None
    classification_review_required: bool
    verification_status: Literal["standard", "review", "suspended"]
    score_ects_basis: dict[str, float] | None
    score_basis_updated_at: datetime | None
    ranking_visible_at: datetime | None
    rejoin_after: datetime | None
    suspended_at: datetime | None
    suspended_reason: str | None


class AdminAccountResponse(ApiModel):
    id: str
    display_name: str
    imt_username: str
    is_disabled: bool
    disabled_at: datetime | None
    disabled_reason: str | None
    last_login_at: datetime | None
    last_sync_at: datetime | None
    last_sync_status: Literal["never", "running", "success", "error"]
    auto_sync_enabled: bool
    auto_sync_interval_hours: Literal[2, 4, 6, 8, 12, 24]
    auto_sync_adaptive: bool
    auto_sync_current_interval_hours: Literal[2, 4, 6, 8, 12, 24]
    auto_sync_paused_reason: Literal["reauth_required"] | None
    auto_sync_paused_at: datetime | None
    created_at: datetime
    session_count: int
    active_token_count: int
    passkey_count: int
    pass_session: ServiceSessionResponse
    tokens: list[AdminTokenResponse]
    leaderboard: AdminLeaderboardResponse


class AdminAccountStatsResponse(ApiModel):
    accounts: int
    disabled: int
    participants: int
    reviews: int


class AdminAccountsResponse(ApiModel):
    stats: AdminAccountStatsResponse
    accounts: list[AdminAccountResponse]


class AdminLearningGrantResponse(ApiModel):
    id: str
    account_id: str
    audience: str
    reason: str
    granted_by_admin_id: str
    granted_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class AdminLearningGrantsResponse(ApiModel):
    grants: list[AdminLearningGrantResponse]


class AdminAccountDeleteResponse(ApiModel):
    deleted: Literal[True]
    id: str
    display_name: str


class AdminAuthThrottleResponse(ApiModel):
    consecutive_failures: int
    blocked_until: datetime | None
    blocked: bool
    retry_after_seconds: int


class AdminPassStatusResponse(ApiModel):
    state: Literal["available", "busy", "resting", "circuit_open"]
    available: bool
    available_at: datetime
    retry_after_seconds: int
    circuit: PassCircuitResponse


class AdminDurationMetricsResponse(ApiModel):
    mean: float | None
    p95: float | None
    worst: float | None


class AdminSessionReuseMetricsResponse(ApiModel):
    hits: int
    successful_operations: int
    hit_rate: float
    full_sso_performed: int
    full_sso_avoided: int


class AdminProfileMetricsResponse(ApiModel):
    fetched: int
    skipped: int


class AdminCompletedDurationResponse(ApiModel):
    median: float | None
    longest: float | None


class AdminSurvivalBucketResponse(ApiModel):
    eligible: int
    survived: int
    rate: float | None


class AdminSurvivalResponse(ApiModel):
    day_1: AdminSurvivalBucketResponse = Field(alias="24h")
    day_3: AdminSurvivalBucketResponse = Field(alias="3d")
    day_7: AdminSurvivalBucketResponse = Field(alias="7d")
    day_30: AdminSurvivalBucketResponse = Field(alias="30d")


class AdminServiceSessionMetricsResponse(ApiModel):
    window_hours: int
    active: int
    reauth_required: int
    hub_ready: int
    established: int
    completed: int
    completed_duration_hours: AdminCompletedDurationResponse
    survival: AdminSurvivalResponse
    end_reasons: dict[str, int]


class AdminPassMetricsResponse(ApiModel):
    window_hours: int
    from_: datetime = Field(alias="from")
    to: datetime
    operations: int
    real_requests: int
    duration_ms: AdminDurationMetricsResponse
    session_reuse: AdminSessionReuseMetricsResponse
    profiles: AdminProfileMetricsResponse
    by_kind: dict[str, int]
    errors: dict[str, int]
    denials: dict[str, int]
    circuit: PassCircuitResponse
    service_sessions: AdminServiceSessionMetricsResponse


class AdminHttpRuntimeMetricsResponse(ApiModel):
    requests: int
    errors: int
    error_rate: float
    average_latency_ms: float
    p95_latency_ms: float


class AdminSseRuntimeMetricsResponse(ApiModel):
    active: int
    opened: int


class AdminQueueMetricsResponse(ApiModel):
    name: Literal["sync", "calendar", "outbox"]
    pending: int
    running: int
    dead_letter: int
    oldest_pending_seconds: int | None


class AdminWorkerMetricsResponse(ApiModel):
    component: Literal["scheduler", "sync", "calendar", "outbox"]
    state: Literal["starting", "ok", "error", "stopping"]
    last_seen_at: datetime
    age_seconds: int
    fresh: bool


class AdminPassOperationalMetricsResponse(ApiModel):
    circuit_state: str
    operations_24h: int
    errors_24h: int
    hourly_quota: int
    daily_quota: int


class AdminCalendarOperationalMetricsResponse(ApiModel):
    attempts_24h: int
    errors_24h: int


class AdminOperationsMetricsResponse(ApiModel):
    generated_at: datetime
    http: AdminHttpRuntimeMetricsResponse
    sse: AdminSseRuntimeMetricsResponse
    queues: list[AdminQueueMetricsResponse]
    workers: list[AdminWorkerMetricsResponse]
    pass_: AdminPassOperationalMetricsResponse = Field(alias="pass")
    calendar: AdminCalendarOperationalMetricsResponse


class AdminPassSessionResponse(ServiceSessionResponse):
    account_id: str
    display_name: str
    imt_username: str
    auto_sync_enabled: bool
    auto_sync_paused_reason: Literal["reauth_required"] | None


class AdminAuditResponse(ApiModel):
    id: int
    action: str
    target_account_id: str | None
    payload: dict[str, Any]
    created_at: datetime
