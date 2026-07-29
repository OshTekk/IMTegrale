# Comparaisons privées V1

État : backend Lot A et interface Lot B développés sur une branche et une draft
PR non publiées. La fonctionnalité reste désactivée par défaut avec
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
6. Une transaction verrouille l'invitation et les deux comptes dans un ordre
   canonique. Une seule acceptation peut réussir.
7. La relation expire au terme choisi, entre 1 et 90 jours, ou est révoquée
   immédiatement par l'un des participants.

L'interface construit le lien avec le secret dans le fragment du navigateur :

```text
/comparisons/accept#invite=SECRET
```

Le fragment n'est pas envoyé dans la requête HTTP ni dans le `Referer`. Le
navigateur l'extrait puis envoie le secret uniquement dans le body d'un `POST`.
Le frontend le retire de l'URL avec `history.replaceState` avant la preview,
sans télémétrie, trace ou stockage persistant. Un rechargement après cet
effacement ne restaure pas le secret : l'utilisateur doit rouvrir le lien
original.

## Interface, session et cache

Les routes frontend sont `/comparisons`, `/comparisons/accept` et
`/comparisons/:publicId`. Le module est chargé à la demande et reste distinct du
Classement. La navigation n'est affichée que lorsque la session expose
`private_comparisons.available=true`, c'est-à-dire pour un propriétaire primaire
actif lorsque le feature flag est ouvert. Cette capacité améliore l'ergonomie ;
les dépendances backend restent la frontière d'autorisation.

Le secret one-shot reste uniquement dans l'état mémoire de la modale qui
l'affiche ou dans une référence éphémère pendant preview, acceptation ou refus.
Ces appels utilisent directement le client TypeScript généré : le secret
n'entre dans aucune query key, aucun cache de query ou mutation, aucun storage,
aucun `history.state`, titre, toast ou libellé accessible. Fermer la modale,
terminer le flux, changer de session ou démonter la page efface la référence.

Les listes et détails utilisent des clés TanStack Query bornées par l'identifiant
du compte courant. Les listes sont toujours revérifiées au focus. Un détail a
`staleTime=0`, `gcTime=0` et est supprimé au démontage, après révocation, après
un `404`, lors d'une déconnexion ou d'un changement de capacité. Aucun cache
persistant ou service worker ne conserve les réponses, qui restent `private,
no-store` côté serveur.

Sur mobile, Comparaisons reste dans le panneau Plus et les résumés/UE communes
sont présentés en cartes sans table comprimée. Les modales restaurent le focus,
les actions principales conservent une cible tactile de 44 px et les règles
`prefers-reduced-motion` sont définies dans la feuille lazy elle-même. La lecture
reste strictement symétrique, sans écart, rang, gagnant ou code couleur
compétitif.

## Consentement version 1

`PRIVATE_COMPARISON_CONSENT_VERSION = 1`. Le créateur consent lors de la
création et le destinataire lors de l'acceptation. Les trois confirmations sont
obligatoires et jamais implicites :

- « Mon identité officielle sera visible par l'autre participant. »
- « Nous verrons nos résumés académiques et nos UE communes, avec moyenne,
  GPA, grade et ECTS, pendant la durée indiquée. Aucun détail d'évaluation
  n'est inclus dans cette version. »
- « Chacun peut révoquer immédiatement la comparaison, mais l'autre participant
  peut recopier ou capturer ce qu'il voit avant la révocation. »

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
lire ou révoquer une comparaison. Les mutations ajoutent Origin et CSRF. Les
`public_id` aléatoires ne sont jamais une autorisation : chaque requête filtre
aussi la relation par le compte de la session.

## Cycle de vie

Une paire de comptes possède au plus une ligne relationnelle. Une invitation
pour une relation déjà active échoue. Après expiration ou révocation, une
nouvelle invitation et deux nouveaux consentements peuvent réactiver cette
ligne ; elle reçoit alors un nouveau `public_id`, ce qui invalide l'ancien lien
de relation. Cette politique évite plusieurs historiques concurrents pour une
même paire.

Une révocation commitée bloque toute lecture suivante. Une expiration est
évaluée à chaque lecture et ne dépend d'aucun worker. La suppression d'un compte
supprime en cascade ses invitations et relations. Les tables ne contiennent
aucune moyenne, note, UE, identité copiée ou autre résultat académique : le
détail est recalculé à la demande avec `calculate_ues` et les fonctions de
pondération existantes.

Un changement de cursus, de promotion, d'identité vérifiée ou de disponibilité
des données rend immédiatement le détail indisponible. La consultation ne
déclenche jamais de synchronisation PASS ou COMPETENCES.

## API V1

| Méthode  | Route                                                 | Effet                                                         |
| -------- | ----------------------------------------------------- | ------------------------------------------------------------- |
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
- le contrôle opérationnel expose uniquement l'état du flag et des compteurs
  agrégés, et alerte sur les lignes incohérentes ou présentes flag fermé ;
- la migration `0029` est additive, ne crée aucune donnée et refuse le downgrade
  lorsqu'une invitation ou relation existe.

Avant une activation future : revoir et fusionner séparément la draft PR,
migrer une base isolée, vérifier zéro ligne, déployer avec le flag fermé,
exécuter les contrôles d'IDOR, concurrence, confidentialité, cache et responsive,
puis décider explicitement de l'ouverture. La V2 éventuelle des évaluations
détaillées exigera un périmètre, une version de consentement et une revue de
menace séparés.

Le modèle de menace dédié est dans
[`docs/security/private-comparisons-threat-model.md`](security/private-comparisons-threat-model.md).
