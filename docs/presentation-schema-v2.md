# Parcours : présentation lecteur et migration du bundle v2

Ce document décrit exclusivement le contrat générique public. Les exemples sont
fictifs et ne doivent pas être remplacés ici par un catalogue, un document ou une
métadonnée pédagogique réelle.

## Compatibilité

Le runtime accepte temporairement les schémas `1` et `2`.

| Bundle              | Index     | Présentation                                                  | État                                           |
| ------------------- | --------- | ------------------------------------------------------------- | ---------------------------------------------- |
| `schema_version: 1` | `json-v1` | `section` et `reader_visibility` dérivés de `kind`            | compatible, aucune migration immédiate requise |
| `schema_version: 2` | `json-v2` | champs de présentation explicites sur chaque nœud pédagogique | format recommandé                              |

Une release v1 déjà active continue donc à être chargée sans modification. Le
passage en v2 doit produire une nouvelle release immuable et un nouvel index ; il
ne faut jamais modifier le dossier actif sur place.

## Champs de présentation

Deux champs sont ajoutés aux nœuds du catalogue :

- `section` organise l'interface : `course`, `practice`, `exam`, `summary`,
  `glossary` ou `sources` ;
- `reader_visibility` choisit la place dans le lecteur : `primary`, `secondary`
  ou `hidden`.

Ces champs ne participent jamais à l'autorisation. Les audiences, droits,
sessions et contrôles backend restent les seules frontières d'accès. En
particulier, `hidden` ne protège pas une ressource et `primary` ne l'autorise pas.

Pour un bundle v1, le runtime applique ces valeurs :

| `kind`              | `section`  | `reader_visibility` |
| ------------------- | ---------- | ------------------- |
| `chapter`, `lesson` | `course`   | `primary`           |
| `exercise`, `pc_td` | `practice` | `primary`           |
| `past_exam`         | `exam`     | `primary`           |
| `concept`           | `glossary` | `secondary`         |
| `source`            | `sources`  | `secondary`         |

Le schéma v2 exige que ces deux champs soient écrits explicitement pour tous les
nœuds pédagogiques listés dans le tableau. Une fiche de synthèse conserve par
exemple un `kind: lesson` mais reçoit `section: summary`. Les nœuds structurels (`audience`, `curriculum`,
`promotion`, `level`, `semester`, `ue`, `module`) peuvent conserver `section:
null`; leur visibilité lecteur reste explicite si le compilateur choisit de
l'exposer.

Les champs optionnels `code` et `description` permettent de distinguer le petit
code stable du titre éditorial. Un titre présenté à l'étudiant doit être un titre
pédagogique propre : ne pas y préfixer un statut, une révision ou un identifiant
de release. Le runtime nettoie encore les anciens préfixes pour la transition,
mais ce comportement n'est pas un format d'auteur.

Exemple minimal exclusivement fictif :

```json
{
  "id": "lesson-synthetic-alpha",
  "kind": "lesson",
  "title": "Lire une relation fictive",
  "code": "LEC-FIC-01",
  "description": "Exemple synthétique sans contenu réel.",
  "section": "course",
  "reader_visibility": "primary"
}
```

Les autres champs obligatoires du nœud v1 (`audience_ids`, relations,
`review_status`, `revision`, `position`, etc.) restent inchangés.

## Procédure exacte de migration

1. Partir de la branche du compilateur privé, sans copier ses entrées dans le
   dépôt public.
2. Conserver la release v1 active et compiler une nouvelle candidate dans un
   autre dossier avec un `release_id` unique.
3. Passer `manifest.schema_version` de `1` à `2`.
4. Passer `search/index.json.schema_version` de `1` à `2` et
   `manifest.search_index.format` de `json-v1` à `json-v2`.
5. Pour chaque nœud `chapter`, `lesson`, `exercise`, `pc_td`, `past_exam`,
   `concept` et `source`, écrire `section` et `reader_visibility`. Utiliser la
   table ci-dessus comme valeur initiale, puis déplacer explicitement les fiches
   vers `summary` si nécessaire.
6. Ajouter, lorsque l'information existe, un `code` court et une `description`
   éditoriale aux UE et modules. Ne pas inventer de titre technique destiné à
   remplacer un vrai titre absent.
7. Retirer des titres les préfixes de fabrication. Conserver `review_status` et
   `revision` comme métadonnées séparées.
8. Garder les concepts en `glossary` et les documents en `sources`; ils ne font
   plus partie de la numérotation du cours.
9. Valider chaque expression mathématique avec KaTeX en mode strict et
   `trust=false`. Depuis `frontend`, le validateur public peut être exécuté sur
   un JSON compilé avec `pnpm validate:learning-math -- /chemin/vers/fichier.json`.
   Les commandes HTML, liens et URI actives sont refusés.
10. Recalculer le nombre de documents, tous les SHA-256 et les tailles seulement
    après la normalisation finale du manifest et de l'index.
11. Exécuter le validateur privé, les tests génériques publics, le scanner de
    frontière pédagogique et l'inventaire de l'archive.
12. Installer la candidate dans un nouveau répertoire immuable, vérifier les
    checksums sur la cible, puis basculer atomiquement `current`.

En cas d'échec, repointer `current` vers la release v1 précédente. Aucune migration
de base n'est nécessaire : la progression reste indexée par les identifiants de
contenu existants. Pour préserver la reprise, conserver les mêmes `content_id`
pour un contenu sémantiquement identique ; un nouvel identifiant crée
volontairement une nouvelle progression.

## Lecteur, mathématiques et documents

Le mode lecteur est le mode par défaut. Il masque les métadonnées de fabrication.
Le mode Revue est une aide visuelle réservée à une session propriétaire primaire,
mais ne remplace aucun contrôle backend.

KaTeX est auto-hébergé. Le composant dédié reçoit uniquement le champ `latex` de
l'AST validé, utilise `trust=false`, le mode strict et un rendu HTML + MathML. Une
erreur affiche sa source seulement en développement ou en Revue ; la production
utilise un fallback neutre.

PDF.js et son worker sont auto-hébergés et chargés après l'ouverture d'un
document. Les assets restent résolus par `asset_id`. Le backend contrôle l'accès
avant l'ouverture du fichier et accepte une seule plage `Range` en v1 : `206`
avec `Content-Range` pour une plage valide, `416` sinon. Les réponses conservent
les headers privés et ne révèlent jamais un chemin de stockage.

Une release `private_preview` conserve ses règles plus strictes existantes :
audience personnelle, droits fermés et aucun asset servi. Le badge « Version de
travail » n'est qu'une présentation unique dans l'en-tête du module.
