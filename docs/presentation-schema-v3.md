# Parcours : bibliothèque personnelle et migration v2 vers v3

Ce document décrit uniquement le contrat générique public. Les identifiants et
exemples sont fictifs. Aucun catalogue ni document pédagogique réel ne doit être
ajouté à ce dépôt.

## Compatibilité

Le runtime accepte les schémas `1`, `2` et `3` :

| Schéma | Index     | Présentation                                     | Recherche lecteur                         |
| ------ | --------- | ------------------------------------------------ | ----------------------------------------- |
| v1     | `json-v1` | valeurs dérivées de `kind`                       | extrait legacy dérivé de `body`           |
| v2     | `json-v2` | `section` et `reader_visibility` explicites      | extrait legacy dérivé de `body`           |
| v3     | `json-v3` | présentation explicite et politique de droits v3 | `reader_excerpt` obligatoire et explicite |

Les bundles v1 et v2 restent chargeables sans réécriture. Leur fallback
d'extrait est conservé uniquement pour la transition. Toute nouvelle release
personnelle doit utiliser v3.

## Mode `personal_library`

`personal_library` désigne une bibliothèque privée destinée à un compte exact.
Ce mode ne constitue jamais une publication et ne rend aucun document
accessible à une cohorte.

Le manifest doit respecter simultanément les règles suivantes :

- `schema_version` vaut `3` et l'index utilise `json-v3` ;
- une seule audience est déclarée et son identifiant commence par `personal:` ;
- tous les nœuds, contenus, sources, assets et droits utilisent cette audience ;
- les statuts de revue du catalogue et des contenus valent `reviewed` ;
- chaque politique de droits autorise l'usage personnel, interdit la publication
  et utilise `basis: requester_private_processing` ;
- aucun `rights_holder` n'est revendiqué pour cette base ;
- chaque asset est référencé, couvert par un checksum et relié aux mêmes droits
  que sa source.

Le runtime n'accepte cette release que lorsque la configuration serveur utilise
`BOTNOTE_LEARNING_ACCESS_MODE=personal` et la même audience exacte. Il exige en
plus toutes les protections déjà appliquées à Parcours : compte actif, session
propriétaire primaire IMT ou passkey, absence de token partagé, preuve
académique ou grant serveur valide, fraîcheur requise, login IMT unique autorisé
et identité d'ingress LAN/Tailnet exacte. Aucun champ envoyé par React ne choisit
le mode, l'audience ou le compte.

## Matrice des modes et droits

| Mode               | Publication | Prévisualisation | Usage personnel | Assets                    | Runtime personnel obligatoire |
| ------------------ | ----------- | ---------------- | --------------- | ------------------------- | ----------------------------- |
| `published`        | oui         | non              | non             | selon droits de diffusion | non                           |
| `private_preview`  | non         | métadonnées      | non             | interdits                 | non                           |
| `personal_library` | non         | non              | oui             | selon droits d'action     | oui                           |

Chaque objet `rights` v3 déclare explicitement :

- `publication_allowed` ;
- `private_preview_allowed` ;
- `personal_use_allowed` ;
- `source_serving_allowed` pour la consultation inline ;
- `download_allowed` pour le téléchargement.

`download_allowed: true` exige toujours `source_serving_allowed: true`. Une
source peut donc être consultable et téléchargeable, consultable sans être
téléchargeable, ou strictement descriptive sans asset. L'existence d'un asset
ne suffit jamais : `/assets/{id}` résout l'action `inline`, tandis que
`/assets/{id}/download` résout l'action `download` avant l'ouverture du
descripteur. Un refus est présenté comme une ressource introuvable.

`private_preview` reste limité aux métadonnées, sans asset et avec tous les
droits de service fermés. `published` reste réservé à une base de publication
démontrée et refuse `requester_private_processing`. Une release
`personal_library` est toujours refusée en mode `cohort`.

Cette séparation n'est pas un DRM. PDF.js doit recevoir les octets d'un document
consultable ; un utilisateur autorisé peut donc les conserver avec les outils de
son navigateur. `download_allowed: false` retire l'action dédiée et refuse la
route avec disposition `attachment`, sans prétendre empêcher la copie d'octets
déjà transmis pour la consultation.

## Recherche v3

Un document de l'index v3 sépare deux champs :

- `body` est le corpus interne utilisé pour le matching côté serveur ;
- `reader_excerpt` est le court extrait envoyé au lecteur.

`reader_excerpt` est obligatoire, limité à 500 caractères et validé comme texte
lecteur. Il ne peut contenir ni HTML, URL active, chemin, identifiant technique,
statut de fabrication ou LaTeX brut. Le titre du document doit être exactement
le titre lecteur de son nœud de catalogue. Le corps de recherche n'est jamais
renvoyé par l'API et n'est jamais tronqué pour fabriquer un extrait v3. Le choix
public est un refus au chargement du bundle ; le compilateur est encouragé à
signaler la même erreur plus tôt, avant la création de la release.

Exemple exclusivement fictif :

```json
{
  "id": "search-synthetic-lesson",
  "catalog_node_id": "lesson-synthetic-alpha",
  "target_id": "content-synthetic-alpha",
  "audience_ids": ["personal:synthetic-reader"],
  "title": "Explorer un modèle fictif",
  "body": "termes internes utiles à la recherche synthétique",
  "reader_excerpt": "Une courte activité fictive pour découvrir le raisonnement proposé."
}
```

## Migration exacte v2 vers v3

1. Conserver la release v2 active et compiler une nouvelle release immuable
   dans un autre dossier avec un nouvel identifiant.
2. Passer le manifest à `schema_version: 3`, l'index à `schema_version: 3` et
   son format à `json-v3`.
3. Pour une bibliothèque personnelle, définir `release_mode:
personal_library` et une seule audience fictivement structurée comme
   `personal:<id>`.
4. Conserver `section` et `reader_visibility` explicites sur tous les nœuds
   pédagogiques. Ne pas utiliser ces champs pour autoriser une ressource.
5. Passer les statuts du catalogue et des contenus personnels à `reviewed`, sans
   préfixer les titres avec ce statut.
6. Ajouter les cinq booléens de politique à chaque entrée `rights`. Pour une
   bibliothèque personnelle, utiliser `publication_allowed: false`,
   `private_preview_allowed: false`, `personal_use_allowed: true` et
   `basis: requester_private_processing`, sans `rights_holder`.
7. Choisir séparément `source_serving_allowed` et `download_allowed` pour chaque
   source. Vérifier que le téléchargement n'est jamais autorisé sans
   consultation.
8. Aligner exactement les audiences de chaque source, asset et politique de
   droits. Référencer chaque asset et conserver le lien source/asset vers la
   même politique.
9. Ajouter un `reader_excerpt` propre à chaque document de recherche et aligner
   son `title` sur le nœud de catalogue. Conserver les termes d'indexation
   uniquement dans `body`.
10. Recalculer `document_count`, tailles et SHA-256 après la sérialisation
    finale. Ne jamais modifier les fichiers après ce calcul.
11. Valider le bundle, les mathématiques, les chemins, liens, audiences, droits,
    checksums et la frontière pédagogique dans l'environnement de build isolé.
12. Configurer côté serveur le mode `personal`, l'audience exacte, un seul login
    IMT et l'allowlist d'ingress privée. Ces valeurs ne vont ni dans le bundle
    public ni dans le frontend.
13. Installer la candidate dans un nouveau répertoire, revérifier les checksums,
    exécuter le smoke-test fictif puis basculer `current` atomiquement.

En cas d'échec, repointer `current` vers la release v2 précédente. Aucune
migration de base n'est requise. Pour préserver la progression, conserver les
`content_id` des contenus sémantiquement identiques ; un nouvel identifiant crée
volontairement une nouvelle progression.

## Réponse API

Les réponses de source et de référence exposent séparément :

- `source_serving_allowed` et `asset_url` ;
- `download_allowed` et `download_url`.

Le frontend utilise ces capacités pour présenter les actions disponibles, mais
ce masquage reste une aide UX. Les deux routes backend refont toujours
l'autorisation du compte, de l'ingress, de l'audience, de la session et de
l'action avant toute lecture.
