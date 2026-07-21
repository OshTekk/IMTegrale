import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  Download,
  FileText,
  Library,
  Play,
  Search,
} from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { learningContentHref, learningResumeHref } from "../lib/learning";
import {
  difficultyLabel,
  readerCatalogTitle,
  readerVisible,
  resolvedLearningSection,
  reviewStatusLabel,
} from "../lib/learningPresentation";
import type { LearningCatalog, LearningCatalogNode, LearningProgress, LearningProgressItem } from "../types";
import { LearningReviewPanel } from "./LearningReviewMode";

interface LearningModuleOverviewProps {
  catalog: LearningCatalog;
  module: LearningCatalogNode;
  ue?: LearningCatalogNode;
  progress?: LearningProgress;
}

function descendantsOf(ancestorId: string, nodes: LearningCatalogNode[]): LearningCatalogNode[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  return nodes.filter((node) => {
    let parentId = node.parent_id;
    const visited = new Set<string>();
    while (parentId && !visited.has(parentId)) {
      if (parentId === ancestorId) return true;
      visited.add(parentId);
      parentId = byId.get(parentId)?.parent_id ?? null;
    }
    return false;
  });
}

function targetId(node: LearningCatalogNode): string | null {
  return node.kind === "source" ? node.source_id : (node.content_id ?? null);
}

function nodeHref(node: LearningCatalogNode): string | null {
  const target = targetId(node);
  return target ? learningContentHref(node.kind, target) : null;
}

function progressItems(progress: LearningProgress | undefined): LearningProgressItem[] {
  if (!progress) return [];
  return Array.isArray(progress) ? (progress as LearningProgressItem[]) : progress.items;
}

function SectionHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <header className="learning-editorial-heading">
      <p className="learning-eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <p>{description}</p>
    </header>
  );
}

function ContentRow({
  node,
  index,
  progress,
  nodes,
  mode,
}: {
  node: LearningCatalogNode;
  index?: number;
  progress?: LearningProgressItem;
  nodes: LearningCatalogNode[];
  mode: "course" | "practice" | "exam" | "summary" | "source";
}) {
  const href = nodeHref(node);
  if (!href) return null;
  const icon = progress?.completed ? (
    <CheckCircle2 aria-label="Terminé" />
  ) : mode === "practice" || mode === "exam" ? (
    <ClipboardCheck aria-hidden="true" />
  ) : mode === "source" ? (
    <FileText aria-hidden="true" />
  ) : (
    <BookOpen aria-hidden="true" />
  );
  const notions = node.prerequisite_ids
    .map((id) => nodes.find((candidate) => candidate.id === id))
    .filter((candidate): candidate is LearningCatalogNode => Boolean(candidate))
    .slice(0, 3);
  const difficulty = difficultyLabel(node.difficulty);
  return (
    <Link className={`learning-editorial-row learning-editorial-row-${mode}`} to={href}>
      <span className="learning-editorial-row-marker">{typeof index === "number" ? <b>{index + 1}</b> : icon}</span>
      <span className="learning-editorial-row-copy">
        <strong>{readerCatalogTitle(node)}</strong>
        <span className="learning-editorial-row-meta">
          {node.estimated_minutes && (
            <span>
              <Clock3 aria-hidden="true" /> {node.estimated_minutes} min
            </span>
          )}
          {difficulty && <span>{difficulty}</span>}
          {mode === "source" && node.document_type && <span>{node.document_type.toUpperCase()}</span>}
          {mode === "source" && node.page_count && (
            <span>
              {node.page_count} page{node.page_count === 1 ? "" : "s"}
            </span>
          )}
        </span>
        {notions.length > 0 && (
          <span className="learning-notions">
            Notions : {notions.map((notion) => readerCatalogTitle(notion)).join(", ")}
          </span>
        )}
      </span>
      <span className="learning-editorial-row-action">
        {mode === "source" && node.download_allowed ? (
          <Download aria-hidden="true" />
        ) : (
          <ArrowRight aria-hidden="true" />
        )}
      </span>
    </Link>
  );
}

export function LearningModuleOverview({ catalog, module, ue, progress }: LearningModuleOverviewProps) {
  const [glossaryQuery, setGlossaryQuery] = useState("");
  const descendants = useMemo(
    () =>
      descendantsOf(module.id, catalog.nodes)
        .filter(readerVisible)
        .sort((a, b) => a.position - b.position),
    [catalog.nodes, module.id],
  );
  const byId = useMemo(() => new Map(catalog.nodes.map((node) => [node.id, node])), [catalog.nodes]);
  const items = descendants.filter((node) => targetId(node));
  const chapters = descendants.filter((node) => node.kind === "chapter");
  const lessons = items.filter((node) => node.kind === "lesson" && resolvedLearningSection(node) === "course");
  const practice = items.filter((node) => resolvedLearningSection(node) === "practice");
  const exams = items.filter((node) => resolvedLearningSection(node) === "exam");
  const summaries = items.filter((node) => resolvedLearningSection(node) === "summary");
  const concepts = items.filter((node) => resolvedLearningSection(node) === "glossary");
  const sources = items.filter((node) => resolvedLearningSection(node) === "sources");
  const progressById = new Map(progressItems(progress).map((item) => [item.content_id, item]));
  const trackedItems = [...lessons, ...practice, ...exams, ...summaries];
  const completedCount = trackedItems.filter((node) => {
    const target = targetId(node);
    return target ? progressById.get(target)?.completed : false;
  }).length;
  const progressPercent = trackedItems.length ? Math.round((completedCount / trackedItems.length) * 100) : 0;
  const resumeCandidates = [...lessons, ...practice];
  const started = resumeCandidates
    .map((node) => {
      const target = targetId(node);
      return target ? { node, progress: progressById.get(target) } : null;
    })
    .filter((entry): entry is { node: LearningCatalogNode; progress: LearningProgressItem } => Boolean(entry?.progress))
    .sort((left, right) => Date.parse(right.progress.updated_at) - Date.parse(left.progress.updated_at));
  const resumeEntry = started[0];
  const firstEntry = resumeCandidates[0] ?? exams[0] ?? summaries[0];
  const firstTarget = firstEntry ? targetId(firstEntry) : null;
  const primaryHref = resumeEntry
    ? learningResumeHref(resumeEntry.node.kind, targetId(resumeEntry.node)!, resumeEntry.progress)
    : firstEntry && firstTarget
      ? learningContentHref(firstEntry.kind, firstTarget)
      : null;
  const moduleTitle = readerCatalogTitle(module);
  const sectionLinks = [
    { id: "continuer", label: "Continuer", visible: Boolean(primaryHref) },
    { id: "comprendre", label: "Comprendre", visible: lessons.length > 0 },
    { id: "entrainer", label: "S'entraîner", visible: practice.length > 0 },
    { id: "annales", label: "Annales", visible: exams.length > 0 },
    { id: "reviser", label: "Réviser", visible: summaries.length > 0 },
    { id: "glossaire", label: "Glossaire", visible: concepts.length > 0 },
    { id: "documents", label: "Documents", visible: sources.length > 0 },
  ].filter((item) => item.visible);
  const chapterFor = (node: LearningCatalogNode) => {
    let parentId = node.parent_id;
    const visited = new Set<string>();
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = byId.get(parentId);
      if (!parent) return null;
      if (parent.kind === "chapter") return parent;
      parentId = parent.parent_id;
    }
    return null;
  };
  const filteredConcepts = concepts.filter((concept) =>
    readerCatalogTitle(concept).toLocaleLowerCase("fr").includes(glossaryQuery.trim().toLocaleLowerCase("fr")),
  );

  return (
    <>
      <header className="learning-module-hero">
        <div className="learning-module-identity">
          <p className="learning-module-code">{module.code?.trim() || "Module"}</p>
          <h1>{moduleTitle}</h1>
          {ue && <p className="learning-module-parent">{readerCatalogTitle(ue)}</p>}
          <p className="learning-module-description">
            {module.description?.trim() ||
              "Un parcours guidé pour comprendre les notions, pratiquer et préparer les évaluations."}
          </p>
          <div className="learning-module-actions">
            {primaryHref && (
              <Link className="primary-button" to={primaryHref}>
                <Play aria-hidden="true" /> {resumeEntry ? "Continuer" : "Commencer"}
              </Link>
            )}
            {catalog.release_mode === "private_preview" && (
              <span className="learning-work-badge">Version de travail</span>
            )}
          </div>
        </div>
        <div className="learning-module-progress" aria-label={`Progression : ${progressPercent} %`}>
          <div
            className="learning-progress-ring"
            style={{ "--learning-progress": `${progressPercent * 3.6}deg` } as CSSProperties}
          >
            <strong>{progressPercent}%</strong>
          </div>
          <span>
            {completedCount} sur {trackedItems.length} terminés
          </span>
        </div>
        <dl className="learning-module-stats">
          <div>
            <dt>Chapitres</dt>
            <dd>{chapters.length}</dd>
          </div>
          <div>
            <dt>Leçons</dt>
            <dd>{lessons.length}</dd>
          </div>
          <div>
            <dt>Exercices</dt>
            <dd>{practice.length}</dd>
          </div>
        </dl>
      </header>

      <LearningReviewPanel
        rows={[
          { label: "Schéma", value: `v${catalog.schema_version}` },
          { label: "Release", value: catalog.release_id },
          { label: "Audience", value: catalog.audience },
          { label: "Identifiant", value: module.id },
          { label: "Statut", value: reviewStatusLabel(module.review_status) },
          { label: "Révision", value: module.revision },
        ]}
      />

      <div className="learning-module-layout">
        <details className="learning-module-toc" open>
          <summary>Dans ce module</summary>
          <nav aria-label="Sommaire du module">
            {sectionLinks.map((item) => (
              <a href={`#${item.id}`} key={item.id}>
                {item.label}
              </a>
            ))}
          </nav>
        </details>
        <div className="learning-module-sections">
          {primaryHref && (
            <section className="learning-editorial-section learning-continue" id="continuer">
              <SectionHeading
                eyebrow="Reprise"
                title="Continuer"
                description={
                  resumeEntry
                    ? "Reprends exactement là où tu t'es arrêté."
                    : "Commence par la première étape conseillée."
                }
              />
              <Link className="learning-continue-row" to={primaryHref}>
                <span>
                  <Play aria-hidden="true" />
                </span>
                <span>
                  <small>{resumeEntry ? "Dernière activité" : "Première étape"}</small>
                  <strong>{readerCatalogTitle(resumeEntry?.node ?? firstEntry!)}</strong>
                  {resumeEntry?.progress.last_section_id && <em>Dernière section enregistrée</em>}
                </span>
                <ArrowRight aria-hidden="true" />
              </Link>
            </section>
          )}

          {lessons.length > 0 && (
            <section className="learning-editorial-section" id="comprendre">
              <SectionHeading
                eyebrow="Cours"
                title="Comprendre"
                description="Des chapitres ordonnés, avec une progression claire leçon par leçon."
              />
              {chapters.map((chapter) => {
                const chapterLessons = lessons.filter((lesson) => chapterFor(lesson)?.id === chapter.id);
                if (!chapterLessons.length) return null;
                return (
                  <section className="learning-chapter" key={chapter.id}>
                    <header>
                      <span>Chapitre</span>
                      <h3>{readerCatalogTitle(chapter)}</h3>
                      <small>
                        {chapterLessons.length} leçon{chapterLessons.length === 1 ? "" : "s"}
                      </small>
                    </header>
                    <div className="learning-editorial-list">
                      {chapterLessons.map((lesson, index) => (
                        <ContentRow
                          key={lesson.id}
                          node={lesson}
                          index={index}
                          progress={progressById.get(targetId(lesson) ?? "")}
                          nodes={catalog.nodes}
                          mode="course"
                        />
                      ))}
                    </div>
                  </section>
                );
              })}
              {lessons.some((lesson) => !chapterFor(lesson)) && (
                <div className="learning-editorial-list">
                  {lessons
                    .filter((lesson) => !chapterFor(lesson))
                    .map((lesson, index) => (
                      <ContentRow
                        key={lesson.id}
                        node={lesson}
                        index={index}
                        progress={progressById.get(targetId(lesson) ?? "")}
                        nodes={catalog.nodes}
                        mode="course"
                      />
                    ))}
                </div>
              )}
            </section>
          )}

          {practice.length > 0 && (
            <section className="learning-editorial-section" id="entrainer">
              <SectionHeading
                eyebrow="Pratique"
                title="S'entraîner"
                description="Exercices et PC/TD avec difficulté, durée et notions mobilisées."
              />
              <div className="learning-editorial-list">
                {practice.map((item) => (
                  <ContentRow
                    key={item.id}
                    node={item}
                    progress={progressById.get(targetId(item) ?? "")}
                    nodes={catalog.nodes}
                    mode="practice"
                  />
                ))}
              </div>
            </section>
          )}

          {exams.length > 0 && (
            <section className="learning-editorial-section" id="annales">
              <SectionHeading
                eyebrow="Évaluation"
                title="Annales"
                description="Les sujets disponibles pour se mettre en conditions."
              />
              <div className="learning-editorial-list">
                {exams.map((item) => (
                  <ContentRow key={item.id} node={item} nodes={catalog.nodes} mode="exam" />
                ))}
              </div>
            </section>
          )}

          {summaries.length > 0 && (
            <section className="learning-editorial-section" id="reviser">
              <SectionHeading
                eyebrow="Synthèse"
                title="Réviser"
                description="Fiches, formulaires et erreurs fréquentes réunis avant l'évaluation."
              />
              <div className="learning-editorial-list">
                {summaries.map((item) => (
                  <ContentRow key={item.id} node={item} nodes={catalog.nodes} mode="summary" />
                ))}
              </div>
            </section>
          )}

          {concepts.length > 0 && (
            <section className="learning-editorial-section" id="glossaire">
              <SectionHeading
                eyebrow="Index"
                title="Glossaire"
                description="Retrouve une définition sans interrompre la séquence du cours."
              />
              <label className="learning-glossary-search">
                <Search aria-hidden="true" />
                <span className="sr-only">Rechercher un concept</span>
                <input
                  type="search"
                  value={glossaryQuery}
                  onChange={(event) => setGlossaryQuery(event.target.value)}
                  placeholder="Rechercher un concept"
                />
              </label>
              <div className="learning-glossary-grid">
                {filteredConcepts.map((concept) => {
                  const href = nodeHref(concept);
                  return href ? (
                    <Link key={concept.id} to={href}>
                      <Library aria-hidden="true" />
                      <span>{readerCatalogTitle(concept)}</span>
                      <ArrowRight aria-hidden="true" />
                    </Link>
                  ) : null;
                })}
              </div>
            </section>
          )}

          {sources.length > 0 && (
            <section className="learning-editorial-section" id="documents">
              <SectionHeading
                eyebrow="Bibliothèque"
                title="Documents"
                description="Sources distinctes du cours, consultables selon leur politique de diffusion."
              />
              <div className="learning-editorial-list">
                {sources.map((source) => (
                  <ContentRow key={source.id} node={source} nodes={catalog.nodes} mode="source" />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
