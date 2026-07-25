# Modèle de menace de la synchronisation autonome

## Portée

Ce document distingue la fondation livrée par G1, la primitive HPKE isolée
livrée par G2 et l'architecture autonome future. Aucun mot de passe IMT n'est
conservé et la table de credentials reste vide. Les seules capacités distantes
actives restent les cookies PASS/HUB chiffrés décrits dans la politique
actuelle.

Les actifs futurs à protéger seront :

- le mot de passe IMT et la capacité de recréer une session CAS ;
- les cookies PASS/HUB ;
- le consentement et la génération du credential ;
- les clés privées de déchiffrement ;
- le compte académique et ses données.

## Frontières de confiance

| Composant | G1 | Cible autonome |
| --- | --- | --- |
| Navigateur | Transmet le mot de passe uniquement à l'authentification | Saisie distincte et consentie lors de l'enrôlement |
| API web | N'importe pas le module HPKE et ne persiste aucun mot de passe | Chiffre avec une clé publique, sans capacité de lecture |
| PostgreSQL | Sessions chiffrées et table credential vide | Enveloppes uniquement, jamais de clé privée |
| Scheduler | Planifie `session_only` depuis le booléen historique | Ne reçoit aucune clé privée |
| Worker sync | Réutilise seulement la session existante | Seul processus autorisé à ouvrir une enveloppe |
| Workers calendar/outbox | Aucun accès au mot de passe | Aucun accès à la clé privée |
| systemd | Environnement commun actuel | Credential privé limité à l'unité sync |

## Scénarios

| Incident | Effet dans G1 | Contrôle G1 | Exigence avant autonomie |
| --- | --- | --- | --- |
| Lecture de PostgreSQL ou fuite d'un dump | Aucun mot de passe présent | Table vide, aucune route d'écriture, primitive HPKE non branchée | Enveloppe standard inutilisable sans clé privée |
| RCE dans le web | Accès possible aux secrets symétriques actuels, pas à un mot de passe stocké | Mode autonome inexécutable | Web limité à la clé publique |
| RCE scheduler/calendar/outbox | Aucun mot de passe stocké | Aucun chemin de lecture | Clé privée absente de leurs unités |
| RCE worker sync | Cookies actuels accessibles selon l'environnement | Aucun credential autonome | Risque résiduel accepté et fortement isolé |
| Token `owner` volé | Ne peut pas changer le mode via la nouvelle route | Propriétaire primaire requis | Enrôlement et suppression sous le même garde |
| Session web primaire volée | Peut choisir `manual` ou `session_only` | Origin, CSRF et révocation existants | Saisie et vérification IMT obligatoires pour enrôler |
| Compte administrateur compromis | Ne peut pas créer de credential | Aucune route admin | L'admin futur ne voit que des états agrégés |
| Accès root au LXC | Contrôle total du runtime et de la mémoire | Hors protection applicative | Risque résiduel non supprimable, durcissement hôte requis |
| Fuite de `/etc/botnote` | Expose les secrets actuels selon les fichiers obtenus | Permissions et fichiers hors Git | Clé privée séparée des secrets généraux |
| Logs, traceback ou télémétrie | Aucun secret autonome à fuiter | Paramètres SQL masqués, payloads sûrs | Corps sensibles jamais journalisés |
| Inspection `/proc`, core dump ou swap | Aucun secret autonome durable | Aucun mot de passe en argument ou environnement | `LoadCredential`, `LimitCORE=0`, durée mémoire minimale |
| Substitution entre comptes | Sans objet dans G1 | FK et relation one-to-one | Liaison cryptographique au compte et à la génération |
| Changement du login IMT | Sans objet dans G1 | Aucun credential | Ancienne enveloppe invalide, réenrôlement requis |
| Rollback d'une sauvegarde | Peut désynchroniser le miroir du mode | Mode effectif dérivé du booléen | Génération et consentement vérifiés avant déchiffrement |
| Perte de clé privée | Aucun impact G1 | Aucune clé créée | Réenrôlement, aucune perte de note |
| Rotation incomplète | Aucun impact G1 | Aucune clé créée | Inventaire par `key_id`, anciennes clés de lecture bornées |
| Révocation pendant un job | Le worker revérifie le booléen et le consentement | Garde avant appel | Nouvelle vérification génération/mode juste avant ouverture |

## Invariants G1

- `manual` et `session_only` sont les seuls modes disponibles.
- `autonomous` est refusé avant toute mutation.
- Le feature flag à `true` empêche le démarrage.
- La migration ne crée aucun credential.
- Aucun modèle persistant ne contient de mot de passe IMT ou de dérivé.
- Aucun payload de settings n'accepte un mot de passe.
- Une valeur `autonomous` injectée en SQL ne produit ni job ni appel PASS.
- Les routes de compatibilité conservent les permissions historiques.

## Invariants G2

- La suite est X25519, HKDF-SHA-256 et ChaCha20-Poly1305 en mode base one-shot.
- Le contexte applicatif passe uniquement par `info`.
- Credential et session possèdent des purposes et contextes distincts.
- Le format binaire v1 refuse versions, longueurs et octets réservés inconnus.
- Le keyring sélectionne une clé par son digest SHA-256 complet, sans essai
  séquentiel.
- Le frame credential possède une taille fixe et reste sous 4 096 octets une
  fois enveloppé.
- Aucune clé, enveloppe ou donnée secrète n'est persistée.
- Aucun routeur, modèle, scheduler, worker ou service PASS n'importe le module.
- HPKE base mode n'apporte ni identité du producteur, ni consentement,
  anti-replay, génération courante ou révocation.

## Risques résiduels

G1 ne réduit pas la capacité déjà détenue par les processus qui chargent la clé
symétrique générale : ils peuvent encore déchiffrer les cookies PASS/HUB selon
leur périmètre actuel. Ce risque est antérieur et doit être traité par G3 et G4.
L'exception locale `owner_managed`, lorsqu'un exploitant l'a volontairement
configurée pour son compte unique, reste également hors du modèle multi-compte
et hors de G1.

Un accès root au LXC, une compromission du binaire du worker sync ou une
inspection de sa mémoire pendant un futur SSO resteront des risques
irréductibles. L'enveloppe asymétrique réduira la surface de déchiffrement, mais
ne rendra jamais l'exploitation d'un mot de passe sans risque.

La présence d'une table vide et d'une primitive isolée ne constitue pas une
capacité autonome. Toute activation avant G3 à G7 doit être considérée comme
une erreur de configuration.
