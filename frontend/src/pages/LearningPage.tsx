import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Bookmark,
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileSearch,
  FolderOpen,
  GraduationCap,
  LibraryBig,
  ListChecks,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";
import { LearningRenderer } from "../components/LearningRenderer";
import { Modal } from "../components/Modal";
import { PassReconnectModal } from "../components/PassReconnectModal";
import {
  learningContentHref,
  learningContentMode,
  learningErrorCopy,
  learningErrorState,
  learningResumeHref,
  learningRouteState,
  safeLearningId,
} from "../lib/learning";
import {
  queryKeys,
  clearAccountState,
  useCreateLearningAttempt,
  useDeleteLearningProgress,
  useLearningAccess,
  useLearningAttempts,
  useLearningCatalog,
  useLearningContent,
  useLearningProgress,
  useLearningReference,
  useLearningSearch,
  useLearningSource,
  useUpdateLearningProgress,
} from "../lib/queries";
import type {
  LearningBlockNode,
  LearningCatalogItem,
  LearningCatalogNode,
  LearningCatalogNodeKind,
  LearningContent,
  LearningInlineNode,
  LearningProgress,
  LearningProgressItem,
  LearningReviewStatus,
  LearningSearchResult,
  LearningSelfAssessment,
  Session,
} from "../types";
import "../learning.css";

const LearningSourceViewer = lazy(() => import("../components/LearningSourceViewer"));

function LearningLoading({ label = "Chargement du parcours" }: { label?: string }) {
  return (
    <div className="learning-loading" role="status" aria-live="polite" aria-busy="true" aria-label={label}>
      <div className="skeleton learning-loading-heading" />
      <div className="skeleton learning-loading-card" />
      <div className="skeleton learning-loading-card" />
    </div>
  );
}

function LearningEmpty({
  icon,
  title,
  message,
  action,
}: {
  icon?: ReactNode;
  title: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="learning-state learning-state-empty" role="status">
      {icon}
      <h2>{title}</h2>
      <p>{message}</p>
      {action}
    </div>
  );
}

function LearningError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const state = learningErrorState(error);
  if (state === "hidden") return <Navigate to="/" replace />;
  const copy = learningErrorCopy[state];
  return (
    <div className="learning-state learning-state-error" role="alert">
      <CircleAlert aria-hidden="true" />
      <h2>{copy.title}</h2>
      <p>{copy.message}</p>
      {onRetry && state !== "reverify" && (
        <button className="secondary-button" type="button" onClick={onRetry}>
          Réessayer
        </button>
      )}
    </div>
  );
}

function progressItems(progress: LearningProgress | undefined): LearningProgressItem[] {
  if (!progress) return [];
  if (Array.isArray(progress)) return progress as LearningProgressItem[];
  return Array.isArray(progress.items) ? progress.items : [];
}

function findProgress(progress: LearningProgress | undefined, contentId: string): LearningProgressItem | undefined {
  return progressItems(progress).find((item) => item.content_id === contentId);
}

function catalogNodesByKind(
  catalog: { nodes: LearningCatalogNode[] },
  kind: LearningCatalogNodeKind,
): LearningCatalogNode[] {
  return catalog.nodes.filter((node) => node.kind === kind).sort((left, right) => left.position - right.position);
}

function isDescendantOf(node: LearningCatalogNode, ancestorId: string, nodes: LearningCatalogNode[]): boolean {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  let parentId = node.parent_id;
  const visited = new Set<string>();
  while (parentId && !visited.has(parentId)) {
    if (parentId === ancestorId) return true;
    visited.add(parentId);
    parentId = byId.get(parentId)?.parent_id ?? null;
  }
  return false;
}

function catalogAncestors(node: LearningCatalogNode, nodes: LearningCatalogNode[]): LearningCatalogNode[] {
  const byId = new Map(nodes.map((item) => [item.id, item]));
  const ancestors: LearningCatalogNode[] = [];
  let parentId = node.parent_id;
  const visited = new Set<string>();
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = byId.get(parentId);
    if (!parent) break;
    ancestors.unshift(parent);
    parentId = parent.parent_id;
  }
  return ancestors;
}

function catalogNodeHref(node: LearningCatalogNode): string | null {
  const targetId = node.kind === "source" ? node.source_id : (node.content_id ?? node.id);
  return targetId ? learningContentHref(node.kind, targetId) : null;
}

function blockInlines(block: LearningBlockNode): LearningInlineNode[] {
  if (block.type === "list") return block.items.flatMap((item) => item.inlines);
  if (block.type === "thematic_break" || block.type === "code" || block.type === "image" || block.type === "math")
    return [];
  return block.inlines;
}

function sourceReferences(
  content: LearningContent,
): Array<{ id: string; source_id: string; page: number; end_page: number | null; label: string | null }> {
  const references = content.blocks
    .flatMap(blockInlines)
    .filter((inline): inline is Extract<LearningInlineNode, { type: "source_ref" }> => inline.type === "source_ref");
  return references
    .filter(
      (reference, index) =>
        references.findIndex(
          (candidate) => candidate.source_id === reference.source_id && candidate.page === reference.page,
        ) === index,
    )
    .map((reference) => ({ ...reference, end_page: reference.end_page ?? null, label: reference.label ?? null }));
}

function ReverificationScreen({ session }: { session: Session }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  return (
    <div className="learning-gate">
      <span className="learning-gate-icon">
        <ShieldCheck aria-hidden="true" />
      </span>
      <p className="learning-eyebrow">Accès pédagogique privé</p>
      <h2>Confirme ton statut étudiant</h2>
      <p>
        Ta dernière vérification IMT a expiré. Une reconnexion ponctuelle suffit ; elle ne lance pas de synchronisation
        des notes.
      </p>
      {(session.learning?.audience_label || session.learning?.level_label) && (
        <div className="learning-gate-labels">
          {session.learning.audience_label && <span>{session.learning.audience_label}</span>}
          {session.learning.level_label && <span>{session.learning.level_label}</span>}
        </div>
      )}
      <button className="primary-button" type="button" onClick={() => setOpen(true)}>
        Vérifier avec mon compte IMT
      </button>
      <PassReconnectModal
        open={open}
        identifier={session.account?.imt_username}
        purpose="learning"
        onClose={() => setOpen(false)}
        onRenewed={() => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.session });
        }}
      />
    </div>
  );
}

function LearningNavigation() {
  return (
    <nav className="learning-local-nav" aria-label="Navigation Parcours">
      <NavLink to="/parcours" end>
        <LibraryBig size={17} /> Accueil
      </NavLink>
      <NavLink to="/parcours/recherche">
        <Search size={17} /> Rechercher
      </NavLink>
      <NavLink to="/parcours/progression">
        <ListChecks size={17} /> Progression
      </NavLink>
    </nav>
  );
}

function LearningWorkspace({ session }: { session: Session }) {
  return (
    <div className="learning-workspace">
      <LearningNavigation />
      <Routes>
        <Route index element={<LearningHome session={session} />} />
        <Route path="ues/:ueId" element={<LearningUePage />} />
        <Route path="modules/:moduleId" element={<LearningModulePage />} />
        <Route path="lecons/:contentId" element={<LearningContentPage mode="lesson" />} />
        <Route path="exercices/:contentId" element={<LearningContentPage mode="exercise" />} />
        <Route path="references/:contentId/:referenceId" element={<LearningReferencePage />} />
        <Route path="sources/:sourceId" element={<LearningSourcePage />} />
        <Route path="recherche" element={<LearningSearchPage />} />
        <Route path="progression" element={<LearningProgressPage />} />
        <Route
          path="*"
          element={
            <LearningEmpty
              title="Page introuvable"
              message="Cette page du parcours n'existe pas."
              action={
                <Link className="secondary-button" to="/parcours">
                  Retour au parcours
                </Link>
              }
            />
          }
        />
      </Routes>
    </div>
  );
}

export function LearningPage({ session }: { session: Session }) {
  const state = learningRouteState(session);
  if (state === "hidden") return <Navigate to="/" replace />;
  if (state === "reverify") return <ReverificationScreen session={session} />;
  return <LearningAccessBoundary session={session} />;
}

function LearningAccessBoundary({ session }: { session: Session }) {
  const queryClient = useQueryClient();
  const access = useLearningAccess(true);
  const nextCatalogVersion = access.data?.catalog_version;
  const needsScopeSwitch =
    access.data?.available === true &&
    typeof nextCatalogVersion === "string" &&
    nextCatalogVersion.length > 0 &&
    session.learning?.catalog_version !== nextCatalogVersion;
  useEffect(() => {
    if (!needsScopeSwitch || !access.data) return;
    clearAccountState(queryClient);
    queryClient.setQueryData<Session>(queryKeys.session, (current) => {
      const samePrimaryAccount =
        current?.authenticated === true &&
        current.account?.id === session.account?.id &&
        current.role === "owner" &&
        (current.auth_method === "imt" || current.auth_method === "passkey");
      return samePrimaryAccount
        ? {
            ...current,
            learning: {
              available: true,
              audience_label: access.data.audience_label,
              level_label: access.data.level_label,
              reverify_required: false,
              catalog_version: nextCatalogVersion,
            },
          }
        : current;
    });
  }, [access.data, needsScopeSwitch, nextCatalogVersion, queryClient, session.account?.id]);
  if (access.isPending) return <LearningLoading label="Vérification de l'accès au parcours" />;
  if (access.isError) {
    if (learningErrorState(access.error) === "reverify") return <ReverificationScreen session={session} />;
    return <LearningError error={access.error} onRetry={() => void access.refetch()} />;
  }
  if (access.data?.available !== true) return <Navigate to="/" replace />;
  if (needsScopeSwitch) return <LearningLoading label="Activation de la nouvelle release du parcours" />;
  return <LearningWorkspace session={session} />;
}

function LearningHome({ session }: { session: Session }) {
  const catalog = useLearningCatalog();
  const progress = useLearningProgress();
  if (catalog.isPending || progress.isPending) return <LearningLoading />;
  if (catalog.isError || progress.isError || !catalog.data)
    return (
      <LearningError
        error={catalog.error ?? progress.error}
        onRetry={() => {
          void catalog.refetch();
          void progress.refetch();
        }}
      />
    );
  const items = progressItems(progress.data);
  const ues = catalogNodesByKind(catalog.data, "ue");
  const started = items.filter((item) => item.updated_at).length;
  const completed = items.filter((item) => item.completed).length;
  return (
    <div className="learning-page learning-home">
      <header className="learning-hero">
        <div>
          <p className="learning-eyebrow">IMTégrale Parcours · espace privé</p>
          <h2>Comprendre, pratiquer, progresser</h2>
          <p>
            Des explications structurées, des exercices guidés et des références exactes vers les sources autorisées.
          </p>
          <div className="learning-hero-labels">
            {session.learning?.audience_label && <span>{session.learning.audience_label}</span>}
            {session.learning?.level_label && <span>{session.learning.level_label}</span>}
          </div>
        </div>
        <GraduationCap aria-hidden="true" />
      </header>

      <section className="learning-summary-grid" aria-label="Résumé de progression">
        <article>
          <BookOpen aria-hidden="true" />
          <strong>{ues.length}</strong>
          <span>UE disponibles</span>
        </article>
        <article>
          <Clock3 aria-hidden="true" />
          <strong>{started}</strong>
          <span>contenus commencés</span>
        </article>
        <article>
          <Check aria-hidden="true" />
          <strong>{completed}</strong>
          <span>contenus terminés</span>
        </article>
      </section>

      <section className="learning-section">
        <header>
          <div>
            <p className="learning-eyebrow">Catalogue</p>
            <h2>Unités d'enseignement</h2>
          </div>
          <Link to="/parcours/recherche">
            Tout rechercher <ArrowRight size={16} />
          </Link>
        </header>
        {ues.length ? (
          <div className="learning-card-grid">
            {ues.map((ue) => {
              const moduleCount = catalogNodesByKind(catalog.data, "module").filter((module) =>
                isDescendantOf(module, ue.id, catalog.data.nodes),
              ).length;
              return (
                <Link className="learning-catalog-card" key={ue.id} to={`/parcours/ues/${encodeURIComponent(ue.id)}`}>
                  <span>
                    <FolderOpen aria-hidden="true" />
                  </span>
                  <div>
                    <h3>{ue.title}</h3>
                    <p>
                      {moduleCount} module{moduleCount === 1 ? "" : "s"}
                    </p>
                  </div>
                  <ChevronRight aria-hidden="true" />
                </Link>
              );
            })}
          </div>
        ) : (
          <LearningEmpty
            icon={<FolderOpen aria-hidden="true" />}
            title="Catalogue vide"
            message="Aucune UE n'est publiée dans cette release courante."
          />
        )}
      </section>
    </div>
  );
}

function LearningUePage() {
  const { ueId: rawUeId } = useParams();
  const ueId = safeLearningId(rawUeId);
  const catalog = useLearningCatalog(Boolean(ueId));
  if (!ueId) return <LearningEmpty title="UE introuvable" message="La référence demandée est invalide." />;
  if (catalog.isPending) return <LearningLoading label="Chargement de l'UE" />;
  if (catalog.isError || !catalog.data)
    return <LearningError error={catalog.error} onRetry={() => void catalog.refetch()} />;
  const ue = catalog.data.nodes.find((item) => item.id === ueId && item.kind === "ue");
  if (!ue)
    return <LearningEmpty title="UE introuvable" message="Cette UE n'est pas publiée dans le catalogue courant." />;
  const modules = catalogNodesByKind(catalog.data, "module").filter((module) =>
    isDescendantOf(module, ue.id, catalog.data.nodes),
  );
  const directContent = catalog.data.nodes
    .filter(
      (item) =>
        Boolean(item.content_id || item.source_id) &&
        isDescendantOf(item, ue.id, catalog.data.nodes) &&
        !catalogAncestors(item, catalog.data.nodes).some((ancestor) => ancestor.kind === "module"),
    )
    .sort((left, right) => left.position - right.position);
  return (
    <div className="learning-page">
      <LearningBreadcrumbs items={[{ label: "Parcours", to: "/parcours" }, { label: ue.title }]} />
      <header className="learning-page-header">
        <p className="learning-eyebrow">Unité d'enseignement</p>
        <h2>{ue.title}</h2>
        <p>Choisis un module pour consulter ses notions, leçons et exercices.</p>
      </header>
      {modules.length ? (
        <div className="learning-list">
          {modules.map((module) => (
            <Link key={module.id} to={`/parcours/modules/${encodeURIComponent(module.id)}`}>
              <span>
                <BookOpen aria-hidden="true" />
              </span>
              <div>
                <h3>{module.title}</h3>
                <p>
                  {
                    catalog.data.nodes.filter(
                      (item) =>
                        (item.content_id || item.source_id) && isDescendantOf(item, module.id, catalog.data.nodes),
                    ).length
                  }{" "}
                  contenus
                </p>
              </div>
              <ChevronRight aria-hidden="true" />
            </Link>
          ))}
        </div>
      ) : null}
      {directContent.length ? (
        <section className="learning-section">
          <header>
            <div>
              <p className="learning-eyebrow">Accès direct</p>
              <h2>Contenus de l'UE</h2>
            </div>
          </header>
          <LearningContentList items={directContent} />
        </section>
      ) : null}
      {!modules.length && !directContent.length ? (
        <LearningEmpty title="UE vide" message="Cette UE ne contient pas encore de contenu publié." />
      ) : null}
    </div>
  );
}

function LearningModulePage() {
  const { moduleId: rawModuleId } = useParams();
  const moduleId = safeLearningId(rawModuleId);
  const catalog = useLearningCatalog(Boolean(moduleId));
  if (!moduleId) return <LearningEmpty title="Module introuvable" message="La référence demandée est invalide." />;
  if (catalog.isPending) return <LearningLoading label="Chargement du module" />;
  if (catalog.isError || !catalog.data)
    return <LearningError error={catalog.error} onRetry={() => void catalog.refetch()} />;
  const module = catalog.data.nodes.find((item) => item.id === moduleId && item.kind === "module");
  if (!module)
    return <LearningEmpty title="Module introuvable" message="Ce module n'est pas publié dans le catalogue courant." />;
  const ue = catalogAncestors(module, catalog.data.nodes).find((item) => item.kind === "ue");
  const items = catalog.data.nodes
    .filter((item) => (item.content_id || item.source_id) && isDescendantOf(item, module.id, catalog.data.nodes))
    .sort((left, right) => left.position - right.position);
  const chapters = catalogNodesByKind(catalog.data, "chapter").filter((chapter) =>
    isDescendantOf(chapter, module.id, catalog.data.nodes),
  );
  const chapterFor = (item: LearningCatalogNode) =>
    [...catalogAncestors(item, catalog.data.nodes)].reverse().find((ancestor) => ancestor.kind === "chapter");
  const ungrouped = items.filter((item) => !chapterFor(item));
  return (
    <div className="learning-page">
      <LearningBreadcrumbs
        items={[
          { label: "Parcours", to: "/parcours" },
          ...(ue ? [{ label: ue.title, to: `/parcours/ues/${encodeURIComponent(ue.id)}` }] : []),
          { label: module.title },
        ]}
      />
      <header className="learning-page-header">
        <p className="learning-eyebrow">Module</p>
        <h2>{module.title}</h2>
        <p>Parcours le contenu dans l'ordre conseillé ou reviens directement à une notion.</p>
      </header>
      {ungrouped.length ? <LearningContentList items={ungrouped} /> : null}
      {chapters.map((chapter) => {
        const chapterItems = items.filter((item) => chapterFor(item)?.id === chapter.id);
        if (!chapterItems.length) return null;
        return (
          <section className="learning-section" key={chapter.id}>
            <header>
              <div>
                <p className="learning-eyebrow">Chapitre</p>
                <h2>{chapter.title}</h2>
              </div>
            </header>
            <LearningContentList items={chapterItems} />
          </section>
        );
      })}
      {!items.length ? (
        <LearningEmpty title="Module vide" message="Aucun contenu n'est encore publié dans ce module." />
      ) : null}
    </div>
  );
}

function LearningContentList({ items }: { items: LearningCatalogItem[] }) {
  return (
    <div className="learning-content-list">
      {items.map((item, index) => {
        const href = catalogNodeHref(item);
        if (!href) return null;
        return (
          <Link key={item.id} to={href}>
            <span className="learning-order">{String(index + 1).padStart(2, "0")}</span>
            <div>
              <small>
                {contentKindLabel(item.kind)}
                {item.review_status !== "published" ? ` · ${reviewStatusLabel(item.review_status)}` : ""}
              </small>
              <h3>{item.title}</h3>
            </div>
            <div className="learning-item-meta">
              {item.estimated_minutes && (
                <span>
                  <Clock3 size={14} /> {item.estimated_minutes} min
                </span>
              )}
              <ChevronRight aria-hidden="true" />
            </div>
          </Link>
        );
      })}
    </div>
  );
}

function contentKindLabel(kind: LearningCatalogNodeKind): string {
  const labels: Record<LearningCatalogNodeKind, string> = {
    audience: "Audience",
    curriculum: "Cursus",
    promotion: "Promotion",
    level: "Niveau",
    semester: "Semestre",
    ue: "UE",
    module: "Module",
    chapter: "Chapitre",
    concept: "Concept",
    lesson: "Leçon",
    exercise: "Exercice",
    pc_td: "PC / TD",
    past_exam: "Annale",
    source: "Source",
  };
  return labels[kind];
}

function reviewStatusLabel(status: LearningReviewStatus): string {
  const labels: Record<LearningReviewStatus, string> = {
    draft: "Brouillon",
    in_review: "En revue",
    reviewed: "Relu",
    private_preview: "Brouillon privé",
    published: "Publié",
    retired: "Retiré",
  };
  return labels[status];
}

function LearningContentPage({ mode }: { mode: "lesson" | "exercise" }) {
  const { contentId: rawContentId } = useParams();
  const location = useLocation();
  const contentId = safeLearningId(rawContentId);
  const content = useLearningContent(contentId);
  const catalog = useLearningCatalog(Boolean(contentId));
  const progress = useLearningProgress(Boolean(contentId));
  const updateProgress = useUpdateLearningProgress();
  const viewedRef = useRef<string | null>(null);
  const releaseRefreshRef = useRef<string | null>(null);
  const refetchCatalog = catalog.refetch;
  const refetchContent = content.refetch;
  const resolvedMode = content.data ? learningContentMode(content.data.kind) : null;
  const releaseMismatch = Boolean(content.data && catalog.data && content.data.release_id !== catalog.data.release_id);
  useEffect(() => {
    if (!releaseMismatch || !content.data || !catalog.data) {
      releaseRefreshRef.current = null;
      return;
    }
    const mismatchKey = `${catalog.data.release_id}:${content.data.release_id}`;
    if (releaseRefreshRef.current === mismatchKey) return;
    releaseRefreshRef.current = mismatchKey;
    void Promise.all([refetchContent(), refetchCatalog()]);
  }, [catalog.data, content.data, refetchCatalog, refetchContent, releaseMismatch]);
  useEffect(() => {
    if (!content.data || resolvedMode !== mode || viewedRef.current === content.data.id) return;
    viewedRef.current = content.data.id;
    updateProgress.mutate({
      contentId: content.data.id,
      update: {
        last_section_id: content.data.blocks.find((block) => block.type === "heading")?.id ?? null,
        exercise_viewed: mode === "exercise" && content.data.kind !== "exercise" ? true : undefined,
      },
    });
  }, [content.data, mode, resolvedMode, updateProgress]);
  useEffect(() => {
    if (!content.data || !location.hash) return;
    let decoded: string;
    try {
      decoded = decodeURIComponent(location.hash.slice(1));
    } catch {
      return;
    }
    const sectionId = safeLearningId(decoded);
    if (!sectionId) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [content.data, location.hash]);
  if (!contentId) return <LearningEmpty title="Contenu introuvable" message="La référence demandée est invalide." />;
  if (content.isPending || catalog.isPending) return <LearningLoading label="Chargement du contenu" />;
  if (content.isError || catalog.isError || !content.data || !catalog.data)
    return (
      <LearningError
        error={content.error ?? catalog.error}
        onRetry={() => {
          void content.refetch();
          void catalog.refetch();
        }}
      />
    );
  if (releaseMismatch) return <LearningLoading label="Alignement de la release du contenu" />;
  if (!resolvedMode)
    return (
      <LearningEmpty
        title="Contenu indisponible"
        message="Ce type de contenu ne possède pas de vue sûre dans cette version."
      />
    );
  if (resolvedMode !== mode) {
    const canonicalHref = learningContentHref(content.data.kind, content.data.id);
    return canonicalHref ? (
      <Navigate to={canonicalHref} replace />
    ) : (
      <LearningEmpty title="Contenu indisponible" message="Ce contenu ne possède pas de route valide." />
    );
  }
  if (progress.isPending) return <LearningLoading label="Chargement de la progression personnelle" />;
  if (progress.isError) return <LearningError error={progress.error} onRetry={() => void progress.refetch()} />;
  const node = catalog.data.nodes.find(
    (item) => item.id === content.data.frontmatter.catalog_node_id || item.content_id === content.data.id,
  );
  const ancestors = node ? catalogAncestors(node, catalog.data.nodes) : [];
  const itemProgress = findProgress(progress.data, content.data.id);
  const view =
    mode === "exercise" ? (
      <LearningExercise
        key={content.data.id}
        content={content.data}
        progress={itemProgress}
        node={node}
        ancestors={ancestors}
      />
    ) : (
      <LearningLesson
        key={content.data.id}
        content={content.data}
        progress={itemProgress}
        node={node}
        ancestors={ancestors}
      />
    );
  return (
    <>
      {updateProgress.isError && (
        <p className="form-error" role="alert">
          La position de lecture n'a pas pu être enregistrée.
        </p>
      )}
      {view}
    </>
  );
}

interface LearningContentViewProps {
  content: LearningContent;
  progress: LearningProgressItem | undefined;
  node: LearningCatalogNode | undefined;
  ancestors: LearningCatalogNode[];
}

function useLearningSectionTracker(contentId: string) {
  const containerRef = useRef<HTMLDivElement>(null);
  const update = useUpdateLearningProgress();
  const mutateRef = useRef(update.mutate);
  const lastSectionRef = useRef<string | null>(null);
  mutateRef.current = update.mutate;
  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof IntersectionObserver === "undefined") return;
    lastSectionRef.current = null;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting && entry.target instanceof HTMLElement)
          .sort((left, right) => Math.abs(left.boundingClientRect.top) - Math.abs(right.boundingClientRect.top));
        const sectionId = visible[0]?.target instanceof HTMLElement ? safeLearningId(visible[0].target.id) : null;
        if (!sectionId || lastSectionRef.current === sectionId) return;
        lastSectionRef.current = sectionId;
        mutateRef.current({ contentId, update: { last_section_id: sectionId } });
      },
      { rootMargin: "-12% 0px -68% 0px", threshold: 0 },
    );
    container
      .querySelectorAll<HTMLElement>("h2[id], h3[id], h4[id], h5[id], h6[id]")
      .forEach((heading) => observer.observe(heading));
    return () => observer.disconnect();
  }, [contentId]);
  return { containerRef, trackingError: update.isError };
}

function LearningLesson({ content, progress, node, ancestors }: LearningContentViewProps) {
  const update = useUpdateLearningProgress();
  const { containerRef: readingRef, trackingError } = useLearningSectionTracker(content.id);
  return (
    <article className="learning-page learning-reading-page">
      <ContentHeader content={content} node={node} ancestors={ancestors} />
      <div className="learning-reading-layout">
        <div className="learning-reading-main" ref={readingRef}>
          <LearningRenderer blocks={content.blocks} contentId={content.id} />
          <SourceReferences content={content} />
        </div>
        <aside className="learning-reading-aside" aria-label="Actions sur la leçon">
          <p className="learning-eyebrow">Ma progression</p>
          <button
            className={progress?.completed ? "learning-check-action active" : "learning-check-action"}
            type="button"
            disabled={update.isPending}
            onClick={() => update.mutate({ contentId: content.id, update: { completed: !progress?.completed } })}
          >
            <Check size={17} /> {progress?.completed ? "Leçon terminée" : "Marquer comme terminée"}
          </button>
          <button
            className={progress?.favorite ? "learning-check-action active" : "learning-check-action"}
            type="button"
            disabled={update.isPending}
            onClick={() => update.mutate({ contentId: content.id, update: { favorite: !progress?.favorite } })}
          >
            <Bookmark size={17} /> {progress?.favorite ? "Dans mes favoris" : "Ajouter aux favoris"}
          </button>
          <SelfAssessment contentId={content.id} value={progress?.self_assessment ?? null} />
          {update.isError && (
            <p className="form-error" role="alert">
              La progression n'a pas pu être enregistrée.
            </p>
          )}
          {trackingError && (
            <p className="form-error" role="alert">
              La dernière section visitée n'a pas pu être enregistrée.
            </p>
          )}
        </aside>
      </div>
    </article>
  );
}

function LearningExercise({ content, progress, node, ancestors }: LearningContentViewProps) {
  const update = useUpdateLearningProgress();
  const attempt = useCreateLearningAttempt();
  const [visibleHints, setVisibleHints] = useState(0);
  const [hintAnnouncement, setHintAnnouncement] = useState("");
  const viewedAttemptRef = useRef<string | null>(null);
  const { containerRef: statementRef, trackingError } = useLearningSectionTracker(content.id);
  const tracksAttempts = content.kind === "exercise";
  useEffect(() => {
    if (!tracksAttempts || viewedAttemptRef.current === content.id) return;
    viewedAttemptRef.current = content.id;
    attempt.mutate({ exercise_id: content.id, attempt_kind: "viewed" });
  }, [attempt, content.id, tracksAttempts]);
  const hints = useMemo(
    () =>
      content.blocks.filter(
        (block): block is Extract<LearningBlockNode, { type: "directive" }> =>
          block.type === "directive" && block.name === "hint",
      ),
    [content.blocks],
  );
  const solutions = content.blocks.filter(
    (block): block is Extract<LearningBlockNode, { type: "directive" }> =>
      block.type === "directive" && block.name === "solution",
  );
  const statement = content.blocks.filter(
    (block) => block.type !== "directive" || (block.name !== "hint" && block.name !== "solution"),
  );
  useEffect(() => {
    const opened = new Set(progress?.opened_hint_ids ?? []);
    let count = 0;
    for (const hint of hints) {
      if (!opened.has(hint.id)) break;
      count += 1;
    }
    setVisibleHints(count);
  }, [content.id, hints, progress?.opened_hint_ids]);
  const openNextHint = () => {
    const next = hints[visibleHints];
    if (!next) return;
    const nextVisibleCount = visibleHints + 1;
    setVisibleHints(nextVisibleCount);
    setHintAnnouncement(`Indice ${nextVisibleCount} ouvert sur ${hints.length}.`);
    const hintId = safeLearningId(next.id);
    if (hintId) {
      if (tracksAttempts) {
        attempt.mutate({ exercise_id: content.id, attempt_kind: "hint_opened", hint_id: hintId });
      } else {
        update.mutate({
          contentId: content.id,
          update: {
            opened_hint_ids: hints.slice(0, nextVisibleCount).map((hint) => hint.id),
          },
        });
      }
    }
  };
  const markCompleted = () => {
    if (tracksAttempts) {
      attempt.mutate({ exercise_id: content.id, attempt_kind: "completed" });
    } else {
      update.mutate({ contentId: content.id, update: { completed: true } });
    }
  };
  return (
    <article className="learning-page learning-exercise-page">
      <ContentHeader content={content} node={node} ancestors={ancestors} />
      <section className="learning-exercise-statement">
        <div ref={statementRef}>
          <LearningRenderer blocks={statement} contentId={content.id} />
        </div>
      </section>
      {hints.length > 0 && (
        <section className="learning-hints">
          <div className="sr-only" role="status" aria-live="polite">
            {hintAnnouncement}
          </div>
          <header>
            <div>
              <p className="learning-eyebrow">Aide progressive</p>
              <h2>Indices</h2>
            </div>
            <span>
              {visibleHints}/{hints.length} ouverts
            </span>
          </header>
          {hints.slice(0, visibleHints).map((hint, index) => (
            <article key={hint.id}>
              <span className="learning-hint-index">
                Indice {index + 1} sur {hints.length}
              </span>
              <LearningRenderer blocks={[hint]} contentId={content.id} />
            </article>
          ))}
          {visibleHints < hints.length && (
            <button
              className="secondary-button"
              type="button"
              onClick={openNextHint}
              disabled={attempt.isPending || update.isPending}
            >
              Ouvrir l'indice {visibleHints + 1}
            </button>
          )}
        </section>
      )}
      <section className="learning-exercise-actions">
        <SelfAssessment
          contentId={content.id}
          value={progress?.self_assessment ?? null}
          disabled={attempt.isPending || update.isPending}
          onSelect={
            tracksAttempts
              ? (value) =>
                  attempt.mutate({ exercise_id: content.id, attempt_kind: "self_assessed", self_assessment: value })
              : undefined
          }
        />
        <button
          className="primary-button"
          type="button"
          onClick={markCompleted}
          disabled={progress?.completed || attempt.isPending || update.isPending}
        >
          <Check size={17} /> {progress?.completed ? "Exercice terminé" : "J'ai terminé"}
        </button>
      </section>
      {(attempt.isError || update.isError) && (
        <p className="form-error" role="alert">
          L'action n'a pas pu être enregistrée.
        </p>
      )}
      {trackingError && (
        <p className="form-error" role="alert">
          La dernière section visitée n'a pas pu être enregistrée.
        </p>
      )}
      {solutions.length > 0 && (
        <details className="learning-solution">
          <summary>Afficher la correction détaillée</summary>
          <LearningRenderer blocks={solutions} contentId={content.id} />
        </details>
      )}
      <SourceReferences content={content} />
    </article>
  );
}

function SelfAssessment({
  contentId,
  value,
  disabled = false,
  onSelect,
}: {
  contentId: string;
  value: LearningSelfAssessment | null;
  disabled?: boolean;
  onSelect?: (value: LearningSelfAssessment) => void;
}) {
  const update = useUpdateLearningProgress();
  const labels: Array<{ value: LearningSelfAssessment; label: string }> = [
    { value: 1, label: "À revoir" },
    { value: 2, label: "Fragile" },
    { value: 3, label: "Compris" },
    { value: 4, label: "Solide" },
    { value: 5, label: "Maîtrisé" },
  ];
  return (
    <fieldset className="learning-self-assessment">
      <legend>Auto-évaluation</legend>
      <div>
        {labels.map((option) => (
          <button
            className={value === option.value ? "active" : ""}
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            disabled={disabled || update.isPending}
            onClick={() => {
              if (onSelect) onSelect(option.value);
              else update.mutate({ contentId, update: { self_assessment: option.value } });
            }}
          >
            {option.label}
          </button>
        ))}
      </div>
      {update.isError && (
        <p className="form-error" role="alert">
          L'auto-évaluation n'a pas pu être enregistrée.
        </p>
      )}
    </fieldset>
  );
}

function ContentHeader({
  content,
  node,
  ancestors,
}: {
  content: LearningContent;
  node: LearningCatalogNode | undefined;
  ancestors: LearningCatalogNode[];
}) {
  const crumbs = ancestors
    .filter((ancestor) => ancestor.kind === "ue" || ancestor.kind === "module")
    .map((ancestor) => ({ label: ancestor.title, to: learningContentHref(ancestor.kind, ancestor.id) ?? undefined }));
  return (
    <>
      <LearningBreadcrumbs
        items={[{ label: "Parcours", to: "/parcours" }, ...crumbs, { label: content.frontmatter.title }]}
      />
      <header className="learning-page-header">
        <p className="learning-eyebrow">{node ? contentKindLabel(node.kind) : "Contenu"}</p>
        <h1>{content.frontmatter.title}</h1>
        <div className="learning-metadata">
          {content.frontmatter.estimated_minutes && (
            <span>
              <Clock3 size={15} /> {content.frontmatter.estimated_minutes} min
            </span>
          )}
          {content.frontmatter.difficulty && <span>Difficulté : {content.frontmatter.difficulty}</span>}
          <span>{reviewStatusLabel(content.frontmatter.review_status)}</span>
          <span>Révision {content.frontmatter.revision}</span>
        </div>
      </header>
    </>
  );
}

function SourceReferences({ content }: { content: LearningContent }) {
  const references = sourceReferences(content);
  if (!references.length) return null;
  return (
    <section className="learning-sources-list" aria-labelledby="learning-sources-heading">
      <h2 id="learning-sources-heading">Références</h2>
      {references.map((reference) => (
        <Link
          key={reference.id}
          to={`/parcours/references/${encodeURIComponent(content.id)}/${encodeURIComponent(reference.id)}`}
        >
          <FileSearch size={17} />
          <span>
            <strong>{reference.label || "Document source"}</strong>
            <small>
              {reference.end_page && reference.end_page !== reference.page
                ? `Pages ${reference.page}–${reference.end_page}`
                : `Page ${reference.page}`}
            </small>
          </span>
          <ChevronRight aria-hidden="true" />
        </Link>
      ))}
    </section>
  );
}

function LearningReferencePage() {
  const { contentId: rawContentId, referenceId: rawReferenceId } = useParams();
  const contentId = safeLearningId(rawContentId);
  const referenceId = safeLearningId(rawReferenceId);
  const reference = useLearningReference(contentId, referenceId);
  if (!contentId || !referenceId)
    return <LearningEmpty title="Référence introuvable" message="La référence demandée est invalide." />;
  if (reference.isPending) return <LearningLoading label="Validation de la référence" />;
  if (reference.isError || !reference.data)
    return <LearningError error={reference.error} onRetry={() => void reference.refetch()} />;
  const sourceId = safeLearningId(reference.data.source_id);
  if (!sourceId || !Number.isInteger(reference.data.page) || reference.data.page < 1) {
    return (
      <LearningEmpty
        title="Référence introuvable"
        message="Cette référence n'est pas disponible dans la release courante."
      />
    );
  }
  return <Navigate to={`/parcours/sources/${encodeURIComponent(sourceId)}?page=${reference.data.page}`} replace />;
}

function LearningSourceCitation({ title, page }: { title: string; page: number | null }) {
  return (
    <section
      className="learning-state learning-state-empty learning-source-citation"
      aria-labelledby="learning-source-citation-heading"
    >
      <FileSearch aria-hidden="true" />
      <h2 id="learning-source-citation-heading">Citation disponible</h2>
      <p>
        <cite>{title}</cite>
        {page ? (
          <>
            {" "}
            — <strong>page {page}</strong>
          </>
        ) : null}
        .
      </p>
      <p>
        Ce document n'est pas proposé à la consultation ou au téléchargement selon sa politique de diffusion. Aucun
        fichier n'est chargé ; cette citation permet de retrouver la page dans une copie autorisée.
      </p>
    </section>
  );
}

function LearningSourcePage() {
  const { sourceId: rawSourceId } = useParams();
  const sourceId = safeLearningId(rawSourceId);
  const [params] = useSearchParams();
  const pageValue = Number(params.get("page"));
  const page = Number.isInteger(pageValue) && pageValue > 0 ? pageValue : null;
  const source = useLearningSource(sourceId);
  const updateProgress = useUpdateLearningProgress();
  const recordedPageRef = useRef<string | null>(null);
  useEffect(() => {
    if (!source.data || !page || page > source.data.page_count) return;
    const key = `${source.data.id}:${page}`;
    if (recordedPageRef.current === key) return;
    recordedPageRef.current = key;
    updateProgress.mutate({ contentId: source.data.id, update: { last_page: page } });
  }, [page, source.data, updateProgress]);
  if (!sourceId) return <LearningEmpty title="Source introuvable" message="La référence demandée est invalide." />;
  if (source.isPending) return <LearningLoading label="Chargement de la source" />;
  if (source.isError || !source.data)
    return <LearningError error={source.error} onRetry={() => void source.refetch()} />;
  const data = source.data;
  const normalizedPage = page && page <= data.page_count ? page : null;
  const sourceServingAllowed = data.source_serving_allowed !== false && Boolean(data.asset_id && data.asset_url);
  return (
    <div className="learning-page learning-source-page">
      <LearningBreadcrumbs
        items={[{ label: "Parcours", to: "/parcours" }, { label: "Source" }, { label: data.title }]}
      />
      <header className="learning-page-header">
        <p className="learning-eyebrow">Document source</p>
        <h1>{data.title}</h1>
        <div className="learning-metadata">
          <span>{data.rights_label}</span>
          <span>
            {data.page_count} page{data.page_count === 1 ? "" : "s"}
          </span>
          <span>Révision {data.revision}</span>
        </div>
      </header>
      {updateProgress.isError && (
        <p className="form-error" role="alert">
          La dernière page visitée n'a pas pu être enregistrée.
        </p>
      )}
      {sourceServingAllowed && data.asset_id ? (
        <Suspense fallback={<LearningLoading label="Ouverture du lecteur" />}>
          <LearningSourceViewer
            assetId={data.asset_id}
            mimeType={data.mime_type}
            title={data.title}
            page={normalizedPage}
          />
        </Suspense>
      ) : (
        <LearningSourceCitation title={data.title} page={normalizedPage} />
      )}
    </div>
  );
}

function LearningSearchPage() {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");
  const search = useLearningSearch(submitted, Boolean(submitted));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = input.trim();
    if (value.length >= 2) setSubmitted(value);
  };
  const results = search.data?.items ?? [];
  return (
    <div className="learning-page learning-search-page">
      <header className="learning-page-header">
        <p className="learning-eyebrow">Recherche transversale</p>
        <h2>Retrouver une notion</h2>
        <p>La recherche reste sur le serveur ; l'index complet n'est jamais envoyé au navigateur.</p>
      </header>
      <form className="learning-search-form" role="search" onSubmit={submit}>
        <label htmlFor="learning-search">Cours, concept, exercice ou source</label>
        <div>
          <Search aria-hidden="true" />
          <input
            id="learning-search"
            type="search"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            minLength={2}
            maxLength={120}
            autoComplete="off"
            placeholder="Ex. transformée, intégrale, signal…"
          />
          <button className="primary-button" type="submit" disabled={input.trim().length < 2 || search.isFetching}>
            Rechercher
          </button>
        </div>
      </form>
      {search.isFetching && <LearningLoading label="Recherche en cours" />}
      {search.isError && <LearningError error={search.error} onRetry={() => void search.refetch()} />}
      {!search.isFetching &&
        submitted &&
        search.data &&
        (results.length ? (
          <section className="learning-search-results" aria-live="polite">
            <h2>
              {search.data.has_more
                ? `${results.length} premiers résultats`
                : `${results.length} résultat${results.length === 1 ? "" : "s"}`}
            </h2>
            {search.data.has_more && (
              <p role="status">D'autres résultats existent. Affine la recherche pour les retrouver.</p>
            )}
            {results.map((result) => (
              <SearchResult key={`${result.entity_type}-${result.entity_id}`} result={result} />
            ))}
          </section>
        ) : (
          <LearningEmpty
            icon={<Search aria-hidden="true" />}
            title="Aucun résultat"
            message="Essaie un terme plus général ou vérifie l'orthographe."
          />
        ))}
      {!submitted && (
        <LearningEmpty
          icon={<Sparkles aria-hidden="true" />}
          title="Le catalogue à portée de recherche"
          message="Saisis au moins deux caractères pour commencer."
        />
      )}
    </div>
  );
}

function SearchResult({ result }: { result: LearningSearchResult }) {
  const href = learningContentHref(result.entity_type, result.entity_id);
  if (!href) return null;
  return (
    <Link to={href}>
      <span>
        <small>{contentKindLabel(result.entity_type)}</small>
        <strong>{result.title}</strong>
        {result.excerpt && <p>{result.excerpt}</p>}
        <i>{result.estimated_minutes ? `${result.estimated_minutes} min` : ""}</i>
      </span>
      <ChevronRight aria-hidden="true" />
    </Link>
  );
}

function LearningProgressPage() {
  const progress = useLearningProgress();
  const catalog = useLearningCatalog();
  const attempts = useLearningAttempts();
  const deleteProgress = useDeleteLearningProgress();
  const [confirmOpen, setConfirmOpen] = useState(false);
  if (progress.isPending || catalog.isPending || attempts.isPending)
    return <LearningLoading label="Chargement de la progression" />;
  if (progress.isError || catalog.isError || attempts.isError)
    return (
      <LearningError
        error={progress.error ?? catalog.error ?? attempts.error}
        onRetry={() => {
          void progress.refetch();
          void catalog.refetch();
          void attempts.refetch();
        }}
      />
    );
  const items = progressItems(progress.data);
  const catalogItems = catalog.data?.nodes ?? [];
  const started = items
    .map((item) => ({
      progress: item,
      content: catalogItems.find(
        (entry) =>
          entry.content_id === item.content_id || entry.source_id === item.content_id || entry.id === item.content_id,
      ),
    }))
    .filter((item) => item.content);
  return (
    <div className="learning-page learning-progress-page">
      <header className="learning-page-header">
        <p className="learning-eyebrow">Données privées</p>
        <h2>Ma progression</h2>
        <p>Ces informations sont visibles uniquement par toi et n'influencent ni tes notes ni le classement.</p>
      </header>
      <section className="learning-summary-grid" aria-label="Résumé de progression">
        <article>
          <BookOpen aria-hidden="true" />
          <strong>{started.length}</strong>
          <span>contenus commencés</span>
        </article>
        <article>
          <Check aria-hidden="true" />
          <strong>{items.filter((item) => item.completed).length}</strong>
          <span>terminés</span>
        </article>
        <article>
          <Bookmark aria-hidden="true" />
          <strong>{items.filter((item) => item.favorite).length}</strong>
          <span>favoris</span>
        </article>
      </section>
      {started.length ? (
        <div className="learning-content-list">
          {started.map(({ progress: item, content }) => {
            const href = learningResumeHref(content!.kind, item.content_id, item);
            return href ? (
              <Link key={item.content_id} to={href}>
                <span className="learning-progress-mark">
                  {item.completed ? <Check aria-label="Terminé" /> : <Clock3 aria-label="En cours" />}
                </span>
                <div>
                  <small>{contentKindLabel(content!.kind)}</small>
                  <h3>{content!.title}</h3>
                  <p>
                    {item.self_assessment
                      ? `Auto-évaluation : ${item.self_assessment}/5`
                      : item.last_page
                        ? `Reprendre à la page ${item.last_page}`
                        : item.last_section_id
                          ? "Reprendre à la dernière section visitée"
                          : "Pas encore auto-évalué"}
                  </p>
                </div>
                <ChevronRight aria-hidden="true" />
              </Link>
            ) : null;
          })}
        </div>
      ) : (
        <LearningEmpty
          title="Aucune progression"
          message="Les contenus consultés apparaîtront ici."
          action={
            <Link className="primary-button" to="/parcours">
              Découvrir le catalogue
            </Link>
          }
        />
      )}
      <section className="learning-privacy-zone">
        <div>
          <RotateCcw aria-hidden="true" />
          <span>
            <strong>Réinitialiser ma progression</strong>
            <small>{attempts.data?.items.length ?? 0} événements d'exercice récents seront aussi supprimés</small>
          </span>
        </div>
        <button
          className="danger-button"
          type="button"
          onClick={() => setConfirmOpen(true)}
          disabled={!items.length || deleteProgress.isPending}
        >
          Tout supprimer
        </button>
      </section>
      <Modal
        open={confirmOpen}
        title="Supprimer toute la progression ?"
        description="Cette action retire les pages visitées, favoris, indices et auto-évaluations de ton compte."
        onClose={() => setConfirmOpen(false)}
        size="small"
      >
        <div className="learning-reset-confirm">
          <p>Le catalogue et tes notes officielles ne seront pas modifiés.</p>
          {deleteProgress.isError && (
            <p className="form-error" role="alert">
              La suppression n'a pas abouti.
            </p>
          )}
          <footer className="modal-actions">
            <button className="secondary-button" type="button" onClick={() => setConfirmOpen(false)}>
              Annuler
            </button>
            <button
              className="danger-button"
              type="button"
              onClick={() => deleteProgress.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })}
              disabled={deleteProgress.isPending}
            >
              Supprimer
            </button>
          </footer>
        </div>
      </Modal>
    </div>
  );
}

function LearningBreadcrumbs({ items }: { items: Array<{ label: string; to?: string }> }) {
  return (
    <nav className="learning-breadcrumbs" aria-label="Fil d'Ariane">
      <ol>
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`}>
            {item.to ? <Link to={item.to}>{item.label}</Link> : <span aria-current="page">{item.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export default LearningPage;
