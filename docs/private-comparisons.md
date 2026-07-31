# Comparaisons privées V1

État : backend Lot A, interface Lot B et remédiation de sécurité C6A développés
sur une branche et une draft PR non publiées. La fonctionnalité reste
désactivée par défaut avec
`BOTNOTE_PRIVATE_COMPARISONS_ENABLED=false`, la migration `0029` n'est pas
déployée et l'activation exige une revue de sécurité et de déploiement séparée.

## Positionnement

Les comparaisons privées sont bilatérales et indépendantes du leaderboard. Le
consentement au classement ne vaut jamais consentement à une comparaison, et
une comparaison ne publie aucune donnée dans le classement. Il n'existe ni
annuaire, ni recherche par nom, login, email, cursus, promotion ou identifiant
de compte.

La V1 partage uniquement :

- l'identité officielle des deux participants ;
- la moyenne générale, le GPA, les ECTS validés, la répartition des grades, la
  fraîcheur et le nombre d'UE officielles ;
- l'intersection exacte des UE selon leur `official_code`, avec intitulé,
  année ou semestre, moyenne, grade, GPA, ECTS et validation de chaque côté.

Elle exclut les évaluations détaillées, coefficients, simulations, contenus
Parcours, agenda, classement, rang, score composite et commentaires. Aucun
gagnant ou perdant n'est calculé.

## Flux d'invitation

1. Un propriétaire principal IMT ou passkey consent au périmètre et crée une
   invitation valable sept jours.
2. L'API retourne une seule fois un secret `pcinv1_…` de 256 bits d'entropie.
3. Seul son HMAC-SHA-256 est conservé, avec le domaine explicite
   `private-comparison-invitation:v1:` et le pepper rotatif existant.
4. Le créateur transmet le lien par un canal de son choix.
5. Le destinataire se connecte comme propriétaire principal, consulte le
   périmètre, puis accepte ou refuse volontairement.
6. Une transaction verrouille la session web, les deux comptes dans l'ordre
   canonique, l'invitation puis la relation. Une seule acceptation peut réussir.
7. La relation expire au terme choisi, entre 1 et 90 jours, ou est révoquée
   immédiatement par l'un des participants.

L'interface construit le lien avec le secret dans le fragment du navigateur :

```text
/comparisons/accept#invite=SECRET
```

Le fragment n'est pas envoyé dans la requête HTTP ni dans le `Referer`. Un
`InvitationFragmentOwner` installé au bootstrap le lit une seule fois et le
retire immédiatement avec `history.replaceState`, avant le routeur, le route
gate, tout rendu et toute lecture de session. Cette procédure s'applique aussi
à une session anonyme, viewer, editor, `owner` token, expirée, désactivée ou
sans feature flag. Un fragment injecté plus tard est également retiré et rend
le bearer initial inutilisable.

Le secret valide peut seulement rester dans la mémoire volatile de ce
propriétaire central. Il n'entre ni dans `localStorage`, `sessionStorage`,
IndexedDB, TanStack Query, `history.state`, l'URL ou le titre. Il n'est livré
qu'une fois à la page d'acceptation, après liaison à l'`auth_epoch` local et au
`session_scope` vérifié. Un rechargement ou toute transition détruit cette
capacité : l'utilisateur doit rouvrir le lien original.

## Interface, session et cache

Les routes frontend sont `/comparisons`, `/comparisons/accept` et
`/comparisons/:publicId`. Le module est chargé à la demande et reste distinct du
Classement. La navigation n'est affichée que lorsque la session expose
`private_comparisons.available=true`, c'est-à-dire pour un propriétaire primaire
actif lorsque le feature flag est ouvert. Cette capacité améliore l'ergonomie ;
les dépendances backend restent la frontière d'autorisation.

`SessionAuthority` est l'unique writer de la session navigateur. Il est créé
pour toute la durée du document, au-dessus de `EpochQueryClientHost`, du
`BrowserRouter` et des routes. Ses états sont `verifying`, `verified`,
`invalidating`, `expired` et `anonymous`. Son `auth_epoch` local et monotone
change avant toute revalidation lors d'une transition de principal, de rôle,
de méthode d'authentification, de génération, de capacité, d'onglet, de
page/BFCache, d'expiration ou d'échec fermé.

Chaque époque possède son propre `QueryClient`. Dès que l'époque change,
l'ancien client annule ses requêtes, vide son `QueryCache` et son
`MutationCache`, puis est démonté ; aucune donnée académique ne passe au client
suivant. L'autorité et son observateur `BroadcastChannel`/`storage` restent
montés même lorsqu'aucune route Comparaisons ne l'est. Les messages ne
contiennent qu'une version, un type et un nonce borné et dédupliqué.

Toutes les lectures de session passent par
`refreshAuthoritativeSession()`. Elles capturent l'époque, une séquence
croissante, un `AbortController` et l'heure monotone de départ. Seule la
dernière séquence de l'époque courante peut publier ; une réponse ou une erreur
tardive ne peut ni restaurer un principal, ni écrire dans le QueryClient, ni
prolonger une deadline.

La deadline conservatrice soustrait le RTT complet à
`session_expires_at - server_time`, puis s'ancre sur `performance.now()` et une
borne murale complémentaire. Pour un même `session_scope`, une réponse peut la
raccourcir mais jamais la repousser. Une heure serveur régressive, une date
absente ou invalide, une durée négative ou excessive, une suspension,
`pagehide`, le BFCache ou le passage hors ligne ferment Comparaisons.

Les listes et détails conservent `staleTime=0`, `gcTime=0` et des réponses
serveur `private, no-store`. Une relation active possède un lease explicite lié
à l'époque, au scope, au `public_id`, à l'expiration, à l'état `active` et à la
dernière validation. Expiration locale, `404`, révocation locale ou distante,
perte de principal/capacité et offline bloquent d'abord le rendu, puis purgent
requêtes, mutations, previews et bearers. L'observer du détail est désactivé
avant le retrait du cache afin qu'un refetch automatique ne puisse pas le
repeupler.

Sur mobile, Comparaisons reste dans le panneau Plus et les résumés/UE communes
sont présentés en cartes sans table comprimée. Les modales restaurent le focus,
les actions principales conservent une cible tactile de 44 px et les règles
`prefers-reduced-motion` sont définies dans la feuille lazy elle-même. La lecture
reste strictement symétrique, sans écart, rang, gagnant ou code couleur
compétitif. Les routes liste, acceptation et détail utilisent toutes le titre
générique « Comparaison privée · IMTégrale » ; l'identité de l'autre
participant reste uniquement dans le contenu autorisé de la page.

## Consentement version 2

`PRIVATE_COMPARISON_CONSENT_VERSION = 2`. La migration `0029`, jamais
déployée, refuse désormais toute version différente. Le créateur consent lors
de la création et le destinataire lors de l'acceptation. Les trois
confirmations sont obligatoires, jamais implicites et toujours précédées du
même manifeste canonique servi par le backend.

`GET /api/v1/private-comparisons/consent-manifest` fournit ce manifeste au
propriétaire principal lorsque le feature flag est ouvert. La création et la
preview renvoient exactement la même structure et le frontend n'entretient
aucune seconde liste de consentement. La création ou l'acceptation reste
impossible si le manifeste est indisponible ou si sa version diverge.

Le manifeste détaille explicitement :

- l'identité officielle des deux participants ;
- chaque champ du résumé général : moyenne, GPA, ECTS validés, nombre d'UE,
  répartition des grades, fraîcheur et dernière vérification ;
- chaque champ des UE communes : code, intitulé, année, semestre, moyenne,
  grade, GPA, ECTS obtenus et alloués, validation et fraîcheur ;
- les dates, le statut, la durée, l'expiration, la révocation immédiate et
  l'historique relationnel minimal ;
- l'absence d'évaluations détaillées, de libellés et coefficients
  d'évaluation, d'UE ou notes non communes, de simulations, agenda, Parcours,
  classement, score, commentaire, donnée tierce et partage public ;
- le risque résiduel de copie ou de capture par l'autre participant.

Les trois confirmations restent :

- « Mon identité officielle sera visible par l'autre participant. »
- « Nous verrons nos résumés académiques et nos UE communes, avec moyenne,
  GPA, grade et ECTS, pendant la durée indiquée. Aucun détail d'évaluation
  n'est inclus dans cette version. »
- « Chacun peut révoquer immédiatement la comparaison, mais l'autre participant
  peut recopier ou capturer ce qu'il voit avant la révocation. »

Un test structurel compare les champs sérialisables de
`PrivateComparisonDetailResponse` aux chemins déclarés par le manifeste. Tout
nouveau champ exige donc une mise à jour explicite du périmètre, de sa copy et
une nouvelle version de consentement avant publication.

Le refus consomme le lien sans révéler au créateur l'identité de la personne qui
l'a refusé. Une invitation transférée peut être utilisée par tout compte
principal éligible qui possède le secret : il ne faut donc l'envoyer qu'au
destinataire voulu.

## Éligibilité et autorisation

Les deux comptes doivent être actifs, posséder une identité et un profil
académique vérifiés, avoir des données officielles PASS/COMPETENCES, et relever
exactement du même cursus et de la même promotion. Une incompatibilité renvoie
une erreur générique sans indiquer la propriété de l'autre compte qui échoue.

Les routes exigent une session propriétaire principale. Les viewers, tokens
`owner`, administrateurs et anonymes ne peuvent ni créer, accepter, refuser,
lire ou révoquer une comparaison. Les mutations ajoutent Origin, CSRF et le
header obligatoire `X-IMTEGRALE-SESSION-BINDING`. Sa valeur est le
`session_scope` opaque attendu par le client ; elle ne constitue pas un secret
d'authentification autonome.

Au début de chaque mutation, puis immédiatement avant le premier effet durable,
le serveur relit la `WebSession` avec `populate_existing=True` et `FOR UPDATE`.
Il revérifie sans message distinctif le compte, le rôle, `auth_method`,
l'absence de délégation, la génération d'accès, l'expiration, l'état actif, le
feature flag et le binding recalculé en comparaison constant-time. Un mismatch
produit `PRIVATE_COMPARISON_SESSION_MISMATCH` sans token, invitation, relation,
consentement, événement ou métrique métier. Les `public_id` aléatoires ne sont
jamais une autorisation : chaque requête filtre aussi la relation par le compte
de la session.

Les événements de cycle de vie `private_comparison:*` suivent la même assurance :
seuls les propriétaires primaires IMT ou passkey peuvent les lire. Une politique
commune filtre la requête du dashboard, son `latest_event_cursor`, le polling et
le flux SSE avant chargement. Les IDs séquentiels restent internes ; les réponses
et reprises SSE emploient des curseurs aléatoires opaques de 192 bits, résolus
dans le compte et la visibilité courants. Un token délégué `owner` ne reçoit
donc ni événement, ni payload, ni trou de séquence permettant d'en déduire
l'existence. Les autres familles d'événements conservent leurs règles de
visibilité antérieures.

## Cycle de vie

Une paire de comptes possède au plus une ligne relationnelle. Une invitation
pour une relation déjà active échoue. Après expiration ou révocation, une
invitation créée strictement après la fin du cycle et deux nouveaux
consentements peuvent réactiver cette ligne ; elle reçoit alors un nouveau
`public_id`, ce qui invalide l'ancien lien de relation. Une invitation créée
avant ou exactement à la fin du cycle devient terminale et ne peut pas effacer
la révocation ni restaurer un consentement antérieur. Cette politique évite
plusieurs historiques concurrents pour une même paire.

Une révocation commitée bloque toute lecture suivante. Elle enregistre pour les
deux participants un événement terminal dont le flux SSE ne sérialise que
`kind` et le `public_id` opaque nécessaires à la purge. Le client bloque le DOM
et retire le cache dans le gestionnaire synchrone avant toute invalidation de
query. Une expiration est évaluée à chaque lecture et ne dépend d'aucun worker.
La suppression d'un compte supprime en cascade ses invitations et relations.
Les tables ne contiennent
aucune moyenne, note, UE, identité copiée ou autre résultat académique : le
détail est recalculé à la demande avec `calculate_ues` et les fonctions de
pondération existantes.

La liste distingue strictement le cycle actif de l'historique terminal. Tant
que le consentement est actif, l'identité et la fraîcheur officielles sont
relues. Une fois la révocation ou l'expiration commitée, la réponse terminale
ne contient plus que `public_id`, `status` et `ended_at` : le compte de l'autre
participant n'est plus lu et aucune identité, fraîcheur, synchronisation ou
donnée académique n'est chargée. Des cycles successifs restent donc des états
relationnels minimaux, sans snapshot personnel.

Chaque décision sensible contourne explicitement l'identity map SQLAlchemy.
L'ordre total est documenté et commun : `WebSession` courante, comptes
participants triés par UUID canonique, invitation, relation. Les transitions
de compte susceptibles de supprimer ou invalider des sessions verrouillent
elles aussi les `WebSession` avant le compte. Le détail et le listing ne
verrouillent pas une session, mais prennent les comptes puis les relations dans
le même ordre. Une révocation commitée avant la lecture est refusée ; une
révocation concurrente attend la fin du snapshot.

Un changement de cursus, de promotion, d'identité vérifiée ou de disponibilité
des données rend immédiatement le détail indisponible. La consultation ne
déclenche jamais de synchronisation PASS ou COMPETENCES.

## Frontière navigateur C6A

La réponse de session authentifiée fournit un `session_scope` HMAC opaque,
`session_expires_at` et `server_time`. Le scope varie avec la session web, le
compte, le rôle, la méthode d'authentification, la délégation, la génération
d'accès et la capacité Comparaisons sans exposer aucun de ces identifiants.
Seul l'état central `verified` rend le sous-arbre sensible.

La topologie est :

```text
SessionAuthorityRoot
└── EpochQueryClientHost
    └── BrowserRouter
        └── Application
```

À l'expiration, lors d'un changement inter-onglets, du passage offline ou d'un
`pagehide`, l'autorité place synchroniquement
`data-session-security` dans un état non vérifié. Le sous-arbre privé est
retiré du DOM — `hidden` et `inert` ne sont que des défenses complémentaires —
avant fetch, annulation ou refetch. Un `pageshow` BFCache garde cette barrière
jusqu'à une revalidation `no-store` complète. Le bearer d'acceptation n'est
jamais reconstruit après retour.

## API V1

| Méthode  | Route                                                 | Effet                                                         |
| -------- | ----------------------------------------------------- | ------------------------------------------------------------- |
| `GET`    | `/api/v1/private-comparisons/consent-manifest`        | Retourne le manifeste canonique V2 sans donnée académique     |
| `POST`   | `/api/v1/private-comparisons/invitations`             | Crée une invitation et retourne le secret une fois            |
| `GET`    | `/api/v1/private-comparisons/invitations`             | Liste les invitations du créateur sans secret ni destinataire |
| `POST`   | `/api/v1/private-comparisons/invitations/preview`     | Prévisualise le créateur et le périmètre avec le secret       |
| `POST`   | `/api/v1/private-comparisons/invitations/accept`      | Accepte et active atomiquement la relation                    |
| `POST`   | `/api/v1/private-comparisons/invitations/decline`     | Invalide le lien sans créer de relation                       |
| `DELETE` | `/api/v1/private-comparisons/invitations/{public_id}` | Révoque une invitation du créateur                            |
| `GET`    | `/api/v1/private-comparisons`                         | Liste uniquement les relations du participant courant         |
| `GET`    | `/api/v1/private-comparisons/{public_id}`             | Calcule le résumé et les UE communes                          |
| `DELETE` | `/api/v1/private-comparisons/{public_id}`             | Révoque immédiatement la relation                             |

Toutes les réponses de cette surface utilisent `Cache-Control: private,
no-store`, `Pragma: no-cache`, `Vary: Cookie`, `X-Content-Type-Options: nosniff`
et `Referrer-Policy: no-referrer`. Lorsque le flag est fermé, le middleware
répond `404` avant le parsing du body et aucune ligne ne peut être créée.

## Limites et exploitation

- cinq invitations actives et vingt créations par compte sur 24 heures ;
- limitation complémentaire par client, sans métrique nominative ;
- aucun token, digest, résultat académique ou identifiant croisé dans les
  événements et logs ;
- événements Comparaisons limités au propriétaire primaire, y compris dans les
  curseurs dashboard et SSE ;
- le contrôle opérationnel expose uniquement l'état du flag et des compteurs
  agrégés, et alerte sur les lignes incohérentes ou présentes flag fermé ;
- la migration `0029` est additive, ne copie aucune donnée personnelle ou
  académique, backfille un curseur aléatoire indépendant pour chaque événement
  existant et refuse le downgrade lorsqu'une invitation ou relation existe.

La remédiation C6A ne traite volontairement pas encore la pseudo-révocation
réversible liée à l'éligibilité, l'oracle de rétention d'événements ni la copy
de consentement propre à chaque acteur ; ils relèvent de C6B. Les constats ZIP,
binaires, Telegram et snapshot de release relèvent de C6C. Aucun nouveau scan
indépendant n'est demandé avant l'achèvement de ces deux lots. Le feature flag
reste faux et la migration `0029` reste non déployée.

Avant une activation future : revoir et fusionner séparément la draft PR,
migrer une base isolée, vérifier zéro ligne, déployer avec le flag fermé,
exécuter les contrôles d'IDOR, concurrence, confidentialité, cache et responsive,
puis décider explicitement de l'ouverture. La V2 éventuelle des évaluations
détaillées exigera un périmètre, une version de consentement et une revue de
menace séparés.

Le modèle de menace dédié est dans
[`docs/security/private-comparisons-threat-model.md`](security/private-comparisons-threat-model.md).
