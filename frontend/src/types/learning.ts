export interface LearningSessionAccess {
  available: boolean;
  audience_label: string | null;
  level_label: string | null;
  reverify_required: boolean;
  catalog_version: string | null;
}

export type LearningCatalogNodeKind =
  | "audience"
  | "curriculum"
  | "promotion"
  | "level"
  | "semester"
  | "ue"
  | "module"
  | "chapter"
  | "concept"
  | "lesson"
  | "exercise"
  | "pc_td"
  | "past_exam"
  | "source";

export type LearningContentKind = Extract<
  LearningCatalogNodeKind,
  "concept" | "lesson" | "exercise" | "pc_td" | "past_exam"
>;
export type LearningDifficulty = "introductory" | "standard" | "advanced";
export type LearningReviewStatus = "draft" | "in_review" | "reviewed" | "private_preview" | "published" | "retired";
export type LearningSection = "course" | "practice" | "exam" | "summary" | "glossary" | "sources";
export type LearningReaderVisibility = "primary" | "secondary" | "hidden";

export interface LearningCatalogNode {
  id: string;
  kind: LearningCatalogNodeKind;
  title: string;
  code: string | null;
  description: string | null;
  parent_id: string | null;
  content_id: string | null;
  source_id: string | null;
  prerequisite_ids: string[];
  difficulty: LearningDifficulty | null;
  estimated_minutes: number | null;
  section: LearningSection | null;
  reader_visibility: LearningReaderVisibility;
  document_type?: "pdf" | "image" | "download" | null;
  page_count?: number | null;
  download_allowed?: boolean;
  review_status: LearningReviewStatus;
  revision: string;
  position: number;
}

export type LearningCatalogItem = LearningCatalogNode;

export interface LearningCatalog {
  schema_version: 1 | 2;
  release_mode: "published" | "private_preview";
  release_id: string;
  catalog_version: string;
  audience: string;
  nodes: LearningCatalogNode[];
}

export interface LearningAccessResponse extends LearningSessionAccess {
  available: true;
  audience: string;
  reverify_required: false;
  catalog_version: string;
  release_id: string;
}

export type LearningInlineMark = "emphasis" | "strong" | "code";

export type LearningInlineNode =
  | { type: "text"; text: string; marks?: LearningInlineMark[] }
  | { type: "math"; latex: string }
  | { type: "line_break" }
  | { type: "source_ref"; id: string; source_id: string; page: number; end_page?: number | null; label?: string | null }
  | { type: "concept_ref"; concept_id: string; label?: string | null }
  | { type: "exercise_ref"; exercise_id: string; label?: string | null };

export type LearningBlockNode =
  | { type: "paragraph"; inlines: LearningInlineNode[] }
  | { type: "heading"; id: string; level: 2 | 3 | 4 | 5 | 6; inlines: LearningInlineNode[] }
  | { type: "list"; ordered: boolean; start?: number | null; items: Array<{ inlines: LearningInlineNode[] }> }
  | { type: "quote"; inlines: LearningInlineNode[] }
  | { type: "code"; code: string; language?: string | null }
  | { type: "math"; latex: string }
  | { type: "image"; asset_id: string; alt_text: string; caption?: string | null }
  | {
      type: "directive";
      id: string;
      name: "note" | "warning" | "definition" | "hint" | "solution";
      title?: string | null;
      inlines: LearningInlineNode[];
    }
  | { type: "thematic_break" };

export interface LearningContentFrontmatter {
  catalog_node_id: string;
  title: string;
  review_status: LearningReviewStatus;
  revision: string;
  prerequisite_ids: string[];
  difficulty: LearningDifficulty | null;
  estimated_minutes: number | null;
}

export interface LearningContent {
  release_id: string;
  id: string;
  kind: LearningContentKind;
  frontmatter: LearningContentFrontmatter;
  blocks: LearningBlockNode[];
}

export interface LearningSource {
  release_id: string;
  id: string;
  title: string;
  asset_id: string | null;
  kind: "image" | "pdf" | "download" | null;
  mime_type: string | null;
  filename: string | null;
  revision: string;
  pages: Array<{ page: number; label: string | null }>;
  page_count: number;
  rights_label: string;
  asset_url: string | null;
  source_serving_allowed?: boolean;
}

export interface LearningSourceReference {
  release_id: string;
  id: string;
  content_id: string;
  source_id: string;
  source_title: string;
  page: number;
  end_page: number | null;
  label: string | null;
  source_url: string;
  asset_url: string | null;
  source_serving_allowed?: boolean;
}

export interface LearningSearchResult {
  entity_id: string;
  catalog_node_id: string;
  entity_type: LearningCatalogNodeKind;
  title: string;
  excerpt: string;
  ue_id: string | null;
  module_id: string | null;
  semester: string | null;
  difficulty: LearningDifficulty | null;
  estimated_minutes: number | null;
}

export interface LearningSearchResponse {
  release_id: string;
  items: LearningSearchResult[];
  has_more: boolean;
  next_offset: null;
}

export type LearningSelfAssessment = 1 | 2 | 3 | 4 | 5;

export interface LearningProgressItem {
  content_id: string;
  last_section_id: string | null;
  last_page: number | null;
  completed: boolean;
  exercise_viewed: boolean;
  opened_hint_ids: string[];
  self_assessment: LearningSelfAssessment | null;
  favorite: boolean;
  created_at: string;
  updated_at: string;
}

export interface LearningProgress {
  catalog_version: string;
  items: LearningProgressItem[];
  summary: {
    started_count: number;
    completed_lessons: number;
    viewed_exercises: number;
    favorite_count: number;
  };
}

export interface LearningProgressUpdate {
  last_section_id?: string | null;
  last_page?: number | null;
  completed?: boolean;
  exercise_viewed?: boolean;
  opened_hint_ids?: string[];
  self_assessment?: LearningSelfAssessment | null;
  favorite?: boolean;
}

export interface LearningAttempt {
  id: string;
  exercise_id: string;
  attempt_kind: "viewed" | "hint_opened" | "self_assessed" | "completed";
  hint_id: string | null;
  self_assessment: LearningSelfAssessment | null;
  attempted_at: string;
}

export type LearningAttemptCreate =
  | { exercise_id: string; attempt_kind: "viewed" | "completed" }
  | { exercise_id: string; attempt_kind: "hint_opened"; hint_id: string }
  | { exercise_id: string; attempt_kind: "self_assessed"; self_assessment: LearningSelfAssessment };

export interface LearningAttemptsResponse {
  items: LearningAttempt[];
}
