# Modèle de menace — Comparaisons privées V1

État : juillet 2026, backend Lot A, frontend Lot B et remédiation C6A non
publiés, migration non déployée et feature flag fermé. Ce document complète le
[modèle général](threat-model.md) sans modifier le consentement ni les
autorisations du leaderboard.

## Actifs et propriétés

| Actif                     | Propriété attendue                                                 |
| ------------------------- | ------------------------------------------------------------------ |
| Secret d'invitation       | 256 bits, retourné une fois, jamais stocké ou journalisé           |
| Digest d'invitation       | HMAC avec séparation de domaine, jamais exposé par l'API           |
| Consentements             | Bilatéraux, versionnés, explicites et liés à une durée             |
| Manifeste de consentement | Canonique, exhaustif et identique pour les deux participants       |
| Relation                  | Limitée à deux comptes distincts, révocable et expirante           |
| Résultats académiques     | Calculés à la demande, jamais copiés dans les nouvelles tables     |
| Identité officielle       | Visible uniquement aux deux participants actifs                    |
| Événements et métriques   | Métadonnées minimales, aucun résultat, token ou paire identifiable |
| Autorité navigateur       | Document-scoped, monotone, writer unique de la session              |
| Binding de mutation       | Opaque, lié à la WebSession fraîche, jamais une authentification    |

## Frontières

```mermaid
flowchart LR
    A["Créateur authentifié"] -->|"POST + CSRF + binding"| API["FastAPI"]
    API -->|"secret une fois"| A
    A -. "canal choisi par l'utilisateur" .-> B["Destinataire"]
    B -->|"fragment local puis POST body"| API
    API -->|"HMAC seulement"| DB[("PostgreSQL")]
    API -->|"calcul à la demande"| Academic["Notes PASS et UE COMPETENCES locales"]
```

Le canal utilisé par le créateur pour transmettre le lien est hors de la
frontière de confiance d'IMTégrale. Le navigateur et tous les identifiants
publics sont non fiables. PostgreSQL est autoritatif pour l'état de
l'invitation et de la relation, mais un `public_id` ne prouve jamais la
participation.

Le bootstrap navigateur possède le fragment avant le routeur. L'autorité de
session se trouve au-dessus du QueryClient d'époque, lui-même au-dessus du
routeur. PostgreSQL reste la seule autorité de session et de mutation :
l'`auth_epoch` n'est jamais envoyé au serveur, tandis que le
`X-IMTEGRALE-SESSION-BINDING` est seulement une attente opaque à comparer à la
session fraîche.

## Menaces et contrôles

| Menace                                               | Impact                                                  | Contrôles V1                                                                                                                                         | Risque résiduel                                                                   |
| ---------------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Énumération des comptes                              | Découverte d'identités ou profils                       | Aucun annuaire ni recherche ; invitation bearer ; erreurs d'éligibilité génériques                                                                   | Le créateur connaît naturellement l'identité après acceptation                    |
| Invitation transférée à un tiers                     | Mauvais participant                                     | Secret à forte entropie, comptes compatibles requis, prévisualisation et consentement explicite                                                      | Tout tiers éligible qui reçoit volontairement le secret peut l'accepter           |
| Token volé                                           | Activation non voulue                                   | TTL sept jours, usage unique, refus/révocation, HMAC seul en base, session primaire exigée                                                           | Compromission conjointe du lien et d'un compte éligible                           |
| Token dans logs ou `Referer`                         | Réutilisation du lien                                   | Propriétaire de fragment au bootstrap, scrub avant route gate/session/render, body POST, `no-referrer`, validation sans echo, événements allowlistés | Le canal de partage tiers peut conserver le lien                                  |
| Token dans l'état ou les caches frontend             | Exposition après le flux ou via outils de développement | Mémoire volatile one-shot liée à l'époque et au scope ; aucun query/mutation TanStack, storage, `history.state`, titre, toast ou console               | Une extension de navigateur compromise peut lire une page ouverte                 |
| Remplacement direct de compte ou BFCache             | Le nouveau principal récupère le bearer ou les données A | Autorité document-scoped, nouvel `auth_epoch`, ancien QueryClient détruit, barrière DOM synchrone, purge à `pagehide`, revalidation au `pageshow`      | Un lien déjà copié volontairement dans le presse-papiers reste hors du contrôle de l'application |
| Réponse de session hors ordre                        | Résurrection du principal ou de la capacité précédente  | Writer unique, séquence croissante, époque capturée, abort et vérification de la dernière requête avant publication                                  | Une panne réseau peut maintenir l'interface fermée                                |
| Réponse retardée réancrant l'expiration              | Confiance locale prolongée au-delà de la session         | RTT intégral soustrait ; même scope seulement raccourcissable ; heure serveur régressive et métadonnées invalides refusées                            | Une nouvelle WebSession légitime crée volontairement un nouveau scope             |
| Double effet React StrictMode                        | Preview ou acceptation répétée                          | Capture unique du fragment, garde de montage et appels utilisateur ponctuels ; tests StrictMode                                                      | Un changement futur du cycle de montage doit conserver ces tests                  |
| Token réutilisé                                      | Relations multiples                                     | Verrou `FOR UPDATE`, état consommé atomique, digest unique                                                                                           | Aucun après commit hors restauration d'une ancienne base                          |
| Double acceptation simultanée                        | Deux relations ou destinataires                         | Verrou invitation, comptes verrouillés dans l'ordre canonique, paire unique, tests PostgreSQL                                                        | Blocage transitoire possible sous forte concurrence                               |
| Auto-invitation                                      | Contournement du consentement bilatéral                 | IDs distincts obligatoires, refus générique avant création relationnelle                                                                             | Aucun connu                                                                       |
| Promotions ou cursus incompatibles                   | Comparaison hors périmètre                              | Segment exact des deux comptes à l'acceptation et à chaque détail                                                                                    | Une erreur de classification amont reste possible                                 |
| Viewer ou token `owner`                              | Accès délégué à des notes croisées                      | `require_primary_owner`/`action`, auth IMT ou passkey, aucun `share_token_id`                                                                        | Vol d'une session primaire active                                                 |
| Preflight A puis POST sous B                         | Mutation durable sous un autre principal                | Header de binding obligatoire sur chaque opération sensible ; mismatch générique avant tout effet                                                    | Aucun connu sans compromission de la session B et du serveur                      |
| Session révoquée pendant l'attente des verrous       | Écriture après révocation                               | `WebSession` et compte relus avec `populate_existing`/`FOR UPDATE` au début puis validés juste avant le premier effet                                  | La transaction qui gagne le verrou définit le point de linéarisation              |
| Administrateur lisant une comparaison                | Accès privilégié indu                                   | Les sessions admin ne sont pas des sessions étudiantes ; aucune route admin de détail                                                                | Root/PostgreSQL peut toujours lire les tables académiques sources                 |
| Accès direct par `public_id` / IDOR                  | Lecture d'une autre paire                               | ID opaque et filtre participant côté serveur ; erreur identique absent/non autorisé                                                                  | Bug futur si une route omet le filtre                                             |
| Révocation pendant une lecture                       | Réponse après retrait                                   | Verrou partagé de la relation pendant le calcul ; révocation exclusive ; aucune lecture commencée après commit                                       | Une réponse déjà reçue ne peut pas être rappelée                                  |
| Expiration pendant une lecture                       | Données après échéance                                  | Vérification sous verrou au début ; expiration à chaque nouvelle lecture                                                                             | Une réponse commencée juste avant l'échéance peut finir                           |
| Compte désactivé ou supprimé                         | Accès persistant                                        | Auth refuse le compte désactivé ; paire revalidée ; FK cascade à la suppression                                                                      | Les copies réalisées avant suppression subsistent chez l'autre participant        |
| Changement de cursus/promotion                       | Ancienne relation devenue incompatible                  | Éligibilité revalidée à chaque détail et statut non actif dans la liste                                                                              | Fenêtre jusqu'à la mise à jour locale des attributs officiels                     |
| Ancien lien après révocation                         | Réactivation involontaire                               | Consentement créateur strictement postérieur à la borne terminale sous verrou ; invitation obsolète terminalisée ; nouveau `public_id`               | Restauration d'une sauvegarde exige une procédure de révocation                   |
| Relation existante et nouvelle invitation            | Relations parallèles                                    | Une paire canonique unique ; invitation obsolète terminalisée ; seule une invitation postérieure au cycle peut réactiver                             | Une invitation transférée reste une capacité jusqu'à son usage ou sa terminaison  |
| Fuite dans événements/métriques                      | Données académiques secondaires                         | Événements sans score, UE, token, digest ou autre compte ; métriques agrégées                                                                        | Un volume très faible peut révéler une utilisation générale à l'opérateur         |
| Token `owner` observant les événements privés        | Déduction d'une invitation ou relation                  | Préfixe `private_comparison:` réservé au propriétaire primaire ; politique unique appliquée aux requêtes dashboard/SSE, au dernier ID visible et au rendu | L'opérateur conserve les seuls agrégats opérationnels explicitement autorisés     |
| Fuite dans traces SQL                                | Secret ou résultat                                      | Paramètres liés ; aucun token brut en base ; logs SQL désactivés en production                                                                       | Un opérateur activant un tracing de bodies pourrait violer la politique           |
| Cache navigateur/proxy                               | Réponse servie après révocation                         | `private, no-store`, `Pragma: no-cache`, `Vary: Cookie`, aucun service worker                                                                        | Le navigateur peut garder une page déjà affichée en mémoire                       |
| Cache React après révocation ou changement de compte | Affichage croisé devenu non autorisé                    | QueryClient par époque, lease explicite, blocage synchrone, observer désactivé avant purge, `gcTime=0`, signal terminal aux deux participants          | Une donnée déjà lue reste mémorisable par le participant                          |
| Ordres de verrous divergents                         | Deadlock ou écriture partielle                           | Plan total `WebSession → comptes triés → invitation → relation`, y compris transitions de compte et tests PostgreSQL concurrents                     | Attente bornée possible sous contention légitime                                 |
| Copy de consentement incomplète                      | Divulgation non comprise par un participant             | Manifeste V2 backend unique, champs inclus et catégories exclues explicites, création et acceptation bloquées si le manifeste manque ou diverge      | Un participant peut ne pas lire intégralement un texte pourtant accessible        |
| Nouveau champ sans nouveau consentement              | Extension silencieuse du périmètre                      | Test structurel entre le modèle de détail et les chemins du manifeste ; changement de périmètre soumis à une nouvelle version de consentement        | Une revue humaine reste nécessaire pour vérifier la qualité de la formulation     |
| Token dans trace, vidéo ou capture                   | Secret durable dans un artefact de test                 | Fixtures synthétiques aléatoires, trace/vidéo/capture désactivées pour les flux E2E one-shot                                                         | Une capture manuelle explicite du lien reste hors contrôle du produit             |
| Capture volontaire                                   | Diffusion hors produit                                  | Copy de consentement explicite et périmètre minimal                                                                                                  | Impossible à empêcher : un participant autorisé peut recopier ou capturer l'écran |

## Concurrence et atomicité

Le plan total unique est :

1. `WebSession` courante pour une mutation ;
2. comptes participants triés par UUID canonique ;
3. invitation ;
4. relation ;
5. lignes dépendantes éventuelles.

L'acceptation lit d'abord des coordonnées non autoritatives, puis prend la
session et les comptes, relit/verrouille l'invitation et enfin la relation. La
consommation et l'activation sont dans le même commit. Révocations et refus
suivent le même ordre. Détail et listing n'ont pas besoin de verrouiller une
session, mais prennent les comptes puis les relations. Les transitions
administratives qui désactivent, révoquent ou suppriment un compte verrouillent
toutes ses `WebSession` dans l'ordre avant le compte et les invitations.

Chaque mutation effectue un preflight court, le libère, puis reconstruit son
plan final. Sous ce plan, elle relit la `WebSession` et les comptes avec
`populate_existing=True` et `FOR UPDATE`, vérifie le binding en temps constant,
puis répète la validation immédiatement avant le premier token, digest,
consentement, relation ou événement durable. Une instance ORM chargée avant un
commit concurrent n'est jamais une preuve d'autorisation.

Scénarios testés : double acceptation du même secret, invitations obsolète et
fraîche concurrentes, deux invitations fraîches concurrentes, acceptation et
révocation simultanées, liste/détail contre révocation, session révoquée contre
acceptation, compte désactivé, ordre inverse artificiel, rollback, révocation
immédiate, lecture tierce, relation expirée, invitation
révoquée/expirée/consommée, auto-invitation, suppression en cascade et
changement de promotion. PostgreSQL vérifie les vrais verrous, timeouts et
contraintes ; SQLite vérifie migration et invariants de schéma.

## Données autorisées et minimisation

Les tables `private_comparison_invitations` et `private_comparisons` ne
contiennent aucune note, moyenne, UE, GPA, ECTS, identité ou simulation. Le
service charge uniquement les notes `source=pass` non archivées et les UE
`metadata_source=competences`, réutilise les calculs backend existants et ne
retient que les codes officiels uniques présents des deux côtés. Il ne déclenche
aucun appel réseau.

Les valeurs manuelles, simulations, détails d'évaluation, contenus Parcours et
données du leaderboard sont absents du contrat V1. Une ambiguïté de code
officiel du même côté retire l'UE de l'intersection au lieu d'effectuer un
rapprochement flou.

Le manifeste de consentement V2 décrit chaque champ sérialisable du détail et
les catégories explicitement exclues. Il est construit une seule fois dans le
contrat backend, exposé par une route `no-store`, repris à l'identique dans la
création et la preview, puis rendu dynamiquement dans les deux parcours. La
version V1 est refusée par Pydantic, le service et les contraintes de la
migration `0029`.

Le client n'utilise la capacité de session que pour la visibilité et le
routage. Une route masquée n'est jamais considérée comme autorisée : chaque
lecture conserve les contrôles backend de participant, session primaire et
feature flag ; chaque mutation ajoute Origin, CSRF et binding. Le serveur
dérive en outre un scope
opaque propre à la session web ; il ne révèle ni identifiant de session ni
secret, mais varie avec le compte, la méthode d'authentification, le rôle, la
délégation, la génération d'accès et la capacité Comparaisons. Le bearer
one-shot n'est rendu que sous son scope créateur et toute réponse arrivée après
un changement de scope est jetée. Le titre du document reste toujours
« Comparaison privée · IMTégrale » et ne contient aucune identité.

Cette liaison est appliquée par `SessionAuthority`, frontière racine à états
`verifying`, `verified`, `invalidating`, `expired` et `anonymous`. Elle possède
seule le droit de publier la session ; `useSession` n'est qu'une projection
read-only dans le QueryClient de l'époque. Le serveur expose l'expiration et
son heure courante ; le client soustrait le RTT complet, ancre la durée sur
l'horloge monotone et conserve une borne murale. Seul `verified` rend le
sous-arbre sensible.

Les signaux inter-onglets, le passage offline, l'expiration, `pagehide` et le
BFCache retirent le DOM avant tout travail asynchrone, abortent les opérations,
détruisent l'ancien QueryClient et purgent caches et bearers. Après un retour
BFCache, une revalidation `no-store` précède tout nouveau rendu ou fetch. Les
événements `private_comparison:revoked|expired` transportent seulement le
`public_id` opaque ; le client bloque le lease et vide le cache avant tout
refetch.

Une relation terminée n'est jamais rejointe à un compte vivant. Son modèle
d'historique expose uniquement l'identifiant relationnel opaque, le statut
terminal et la date de fin ; il ne conserve ni identité, ni fraîcheur, ni
résultat, ni snapshot personnel.

Les événements de cycle de vie sont soumis à une assurance distincte des
événements historiquement visibles par un rôle `owner`. Le serveur dérive
`primary_owner` depuis le contexte d'authentification autoritatif, puis applique
une seule politique à la sélection SQL, au `latest_event_cursor`, au dashboard
et au flux SSE. Les IDs ordonnés ne quittent pas le serveur ; un curseur
aléatoire de 192 bits est résolu avec le compte et cette même politique. Un
token délégué ne peut donc ni lire ces événements ni observer un trou de
séquence causé par eux.

## Hypothèses et risques acceptés

- le pepper HMAC reste hors PostgreSQL et suit la rotation existante ;
- le proxy ne journalise pas les bodies et conserve `Referrer-Policy` ;
- les comptes, profils et sources académiques officiels sont correctement
  classifiés ;
- le participant choisit un canal de partage approprié ;
- un participant autorisé peut toujours mémoriser, recopier, photographier ou
  rediffuser les données affichées ; la révocation ne peut pas effacer ces
  copies ;
- une compromission root ou applicative complète dépasse la séparation logique
  entre deux comptes et doit être traitée comme incident d'instance.

Une V2 exposant des évaluations détaillées est hors périmètre. Elle exige une
nouvelle version de consentement, une analyse de minimisation et une revue de ce
modèle avant tout code.

## Périmètre restant après C6A

C6A ferme l'autorité de session, le fragment, les réponses hors ordre, la
deadline, le binding/rebind, l'ordre des verrous et la purge terminale. Il ne
ferme pas la pseudo-révocation réversible liée à l'éligibilité, l'oracle de
rétention des événements ni la copy de consentement propre à chaque acteur :
ces sujets appartiennent à C6B. Les constats ZIP, binaires, Telegram et snapshot
de release appartiennent à C6C. Le feature flag reste faux, `0029` reste non
déployée et aucun nouveau scan indépendant n'est requis avant ces deux lots.
