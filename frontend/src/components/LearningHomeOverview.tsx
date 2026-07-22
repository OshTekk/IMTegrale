import { ArrowRight, Bookmark, BookOpen, CheckCircle2, Clock3, FolderOpen, Search, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { learningContentHref, learningResumeHref } from "../lib/learning";
import { readerAudienceSubtitle, readerCatalogTitle, readerVisible } from "../lib/learningPresentation";
import type { LearningCatalog, LearningCatalogNode, LearningProgress, LearningProgressItem } from "../types";

function isDescendant(node: LearningCatalogNode, ancestorId: string, nodes: LearningCatalogNode[]) {
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

function targetId(node: LearningCatalogNode) {
  return node.kind === "source" ? node.source_id : node.content_id;
}

function hrefFor(node: LearningCatalogNode, progress?: LearningProgressItem) {
  const target = targetId(node);
  if (!target) return null;
  return progress ? learningResumeHref(node.kind, target, progress) : learningContentHref(node.kind, target);
}

function ActivityLink({ node, progress }: { node: LearningCatalogNode; progress: LearningProgressItem }) {
  const href = hrefFor(node, progress);
  if (!href) return null;
  return (
    <Link className="learning-activity-row" to={href}>
      <span>{progress.completed ? <CheckCircle2 aria-label="Terminé" /> : <Clock3 aria-label="En cours" />}</span>
      <span>
        <strong>{readerCatalogTitle(node)}</strong>
        <small>
          {progress.last_page
            ? `Page ${progress.last_page}`
            : progress.last_section_id
              ? "Dernière section enregistrée"
              : "Activité récente"}
        </small>
      </span>
      <ArrowRight aria-hidden="true" />
    </Link>
  );
}

export function LearningHomeOverview({
  catalog,
  progress,
  audienceLabel,
  levelLabel,
}: {
  catalog: LearningCatalog;
  progress: LearningProgress;
  audienceLabel: string | null | undefined;
  levelLabel: string | null | undefined;
}) {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const visibleNodes = catalog.nodes.filter(readerVisible);
  const ues = visibleNodes.filter((node) => node.kind === "ue").sort((a, b) => a.position - b.position);
  const progressItems = progress.items ?? [];
  const progressById = new Map(progressItems.map((item) => [item.content_id, item]));
  const activities = progressItems
    .map((item) => ({
      progress: item,
      node: visibleNodes.find((node) => targetId(node) === item.content_id),
    }))
    .filter((item): item is { progress: LearningProgressItem; node: LearningCatalogNode } => Boolean(item.node))
    .sort((left, right) => Date.parse(right.progress.updated_at) - Date.parse(left.progress.updated_at));
  const resume = activities.find((item) => !item.progress.completed) ?? activities[0];
  const review = activities.filter(
    (item) => item.progress.self_assessment !== null && item.progress.self_assessment <= 2,
  );
  const favorites = activities.filter((item) => item.progress.favorite);
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    const query = search.trim();
    if (query.length >= 2) navigate(`/parcours/recherche?q=${encodeURIComponent(query)}`);
  };

  return (
    <div className="learning-home-editorial">
      <header className="learning-home-intro">
        <div>
          <p className="learning-eyebrow">{readerAudienceSubtitle(audienceLabel, levelLabel)}</p>
          <h1>Apprendre avec un fil clair</h1>
          <p>Reprends une leçon, entraîne-toi ou retrouve une notion sans parcourir tout le catalogue.</p>
        </div>
        <form className="learning-home-search" role="search" onSubmit={submitSearch}>
          <Search aria-hidden="true" />
          <label className="sr-only" htmlFor="learning-home-search">
            Rechercher dans Parcours
          </label>
          <input
            id="learning-home-search"
            type="search"
            value={search}
            minLength={2}
            maxLength={120}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Rechercher une notion, une leçon…"
          />
          <button type="submit" aria-label="Lancer la recherche" disabled={search.trim().length < 2}>
            <ArrowRight aria-hidden="true" />
          </button>
        </form>
      </header>

      <section className="learning-home-band" aria-labelledby="learning-home-continue">
        <header>
          <div>
            <p className="learning-eyebrow">Reprise</p>
            <h2 id="learning-home-continue">Continuer là où je me suis arrêté</h2>
          </div>
          <Link to="/parcours/progression">Toute ma progression</Link>
        </header>
        {resume ? (
          <ActivityLink node={resume.node} progress={resume.progress} />
        ) : (
          <div className="learning-inline-empty">
            <Sparkles aria-hidden="true" />
            <span>
              <strong>Ton parcours commence ici</strong>
              <small>Ouvre une UE pour choisir ta première leçon.</small>
            </span>
          </div>
        )}
      </section>

      <section className="learning-home-band" aria-labelledby="learning-home-ues">
        <header>
          <div>
            <p className="learning-eyebrow">Catalogue</p>
            <h2 id="learning-home-ues">UE disponibles</h2>
          </div>
          <span>{ues.length} UE</span>
        </header>
        {ues.length ? (
          <div className="learning-ue-grid">
            {ues.map((ue) => {
              const modules = visibleNodes.filter(
                (node) => node.kind === "module" && isDescendant(node, ue.id, visibleNodes),
              );
              const ueContent = visibleNodes.filter(
                (node) => targetId(node) && isDescendant(node, ue.id, visibleNodes),
              );
              const completed = ueContent.filter((node) => {
                const target = targetId(node);
                return target ? progressById.get(target)?.completed : false;
              }).length;
              const percent = ueContent.length ? Math.round((completed / ueContent.length) * 100) : 0;
              return (
                <Link className="learning-ue-card" key={ue.id} to={`/parcours/ues/${encodeURIComponent(ue.id)}`}>
                  <span className="learning-ue-icon">
                    <FolderOpen aria-hidden="true" />
                  </span>
                  <span>
                    {ue.code && <small>{ue.code}</small>}
                    <strong>{readerCatalogTitle(ue)}</strong>
                    <em>
                      {modules.length} module{modules.length === 1 ? "" : "s"} · {percent}% terminé
                    </em>
                  </span>
                  <ArrowRight aria-hidden="true" />
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="learning-inline-empty">
            <FolderOpen aria-hidden="true" />
            <span>
              <strong>Catalogue vide</strong>
              <small>Aucune UE n'est disponible pour le moment.</small>
            </span>
          </div>
        )}
      </section>

      <div className="learning-home-columns">
        <section className="learning-home-band" aria-labelledby="learning-home-review">
          <header>
            <div>
              <p className="learning-eyebrow">Consolidation</p>
              <h2 id="learning-home-review">À revoir</h2>
            </div>
          </header>
          {review.length ? (
            review
              .slice(0, 3)
              .map((item) => <ActivityLink key={item.node.id} node={item.node} progress={item.progress} />)
          ) : (
            <div className="learning-inline-empty compact">
              <BookOpen aria-hidden="true" />
              <span>Aucune activité signalée comme fragile.</span>
            </div>
          )}
        </section>
        <section className="learning-home-band" aria-labelledby="learning-home-favorites">
          <header>
            <div>
              <p className="learning-eyebrow">Raccourcis</p>
              <h2 id="learning-home-favorites">Favoris</h2>
            </div>
          </header>
          {favorites.length ? (
            favorites
              .slice(0, 3)
              .map((item) => <ActivityLink key={item.node.id} node={item.node} progress={item.progress} />)
          ) : (
            <div className="learning-inline-empty compact">
              <Bookmark aria-hidden="true" />
              <span>Les leçons épinglées apparaîtront ici.</span>
            </div>
          )}
        </section>
      </div>

      <section className="learning-home-band learning-recent-progress" aria-labelledby="learning-home-recent">
        <header>
          <div>
            <p className="learning-eyebrow">Activité</p>
            <h2 id="learning-home-recent">Progression récente</h2>
          </div>
        </header>
        <div className="learning-recent-track">
          {activities.slice(0, 5).map((item) => (
            <span key={item.node.id} title={readerCatalogTitle(item.node)}>
              <i className={item.progress.completed ? "complete" : "started"} />
              {readerCatalogTitle(item.node)}
            </span>
          ))}
          {!activities.length && <span>Aucune activité récente.</span>}
        </div>
      </section>
    </div>
  );
}
