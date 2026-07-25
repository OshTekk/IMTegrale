# Modèle de menace de la synchronisation autonome

## Portée

Ce document distingue la fondation G1, la primitive HPKE G2, la frontière
worker G3, la migration de sessions G4 et l'architecture autonome future. Aucun
mot de passe IMT n'est conservé et la table de credentials reste vide. Les
seules capacités distantes actives restent les cookies PASS/HUB protégés par
HPKE et décrits dans la politique actuelle.

Les actifs futurs à protéger seront :

- le mot de passe IMT et la capacité de recréer une session CAS ;
- les cookies PASS/HUB ;
- le consentement et la génération du credential ;
- les clés privées de déchiffrement ;
- le compte académique et ses données.

## Frontières de confiance

| Composant | G3 | Cible autonome |
| --- | --- | --- |
| Navigateur | Transmet le mot de passe uniquement à l'authentification | Saisie distincte et consentie lors de l'enrôlement |
| API web | Reçoit uniquement la clé publique des sessions et ne persiste aucun mot de passe | Chiffre avec une clé publique, sans capacité de lecture |
| PostgreSQL | Enveloppes de sessions et table credential vide | Enveloppes uniquement, jamais de clé privée |
| Scheduler | Planifie `session_only` depuis le booléen historique | Ne reçoit aucune clé privée |
| Worker sync | Identité dédiée, ouvre les sessions PASS/HUB | Seul processus autorisé à ouvrir une enveloppe métier |
| Workers calendar/outbox | Aucun accès au mot de passe | Aucun accès à la clé privée |
| systemd | Credentials privés limités à l'unité sync | Même frontière avec rotation |

## Scénarios

| Incident | Effet dans G1 | Contrôle G1 | Exigence avant autonomie |
| --- | --- | --- | --- |
| Lecture de PostgreSQL ou fuite d'un dump | Aucun mot de passe présent ; sessions sous enveloppes | Clé privée absente de PostgreSQL et table credential vide | Enveloppe inutilisable sans clé privée |
| RCE dans le web | Peut voir un mot de passe pendant une connexion directe et sceller une session, mais ne peut pas ouvrir une session persistée | Web limité à la clé publique session ; mode autonome inexécutable | Même séparation pour les futurs credentials |
| RCE scheduler/calendar/outbox | Aucun mot de passe stocké | Aucun chemin de lecture | Clé privée absente de leurs unités |
| RCE worker sync | Cookies actuels accessibles selon l'environnement | Aucun credential autonome | Risque résiduel accepté et fortement isolé |
| Token `owner` volé | Ne peut pas changer le mode via la nouvelle route | Propriétaire primaire requis | Enrôlement et suppression sous le même garde |
| Session web primaire volée | Peut choisir `manual` ou `session_only` | Origin, CSRF et révocation existants | Saisie et vérification IMT obligatoires pour enrôler |
| Compte administrateur compromis | Ne peut pas créer de credential | Aucune route admin | L'admin futur ne voit que des états agrégés |
| Accès root au LXC | Contrôle total du runtime et de la mémoire | Hors protection applicative | Risque résiduel non supprimable, durcissement hôte requis |
| Fuite de `/etc/botnote` | Dépend des fichiers obtenus | Clés privées root-only et injectées uniquement au worker | Clé privée credential séparée des secrets généraux |
| Logs, traceback ou télémétrie | Aucun secret autonome à fuiter | Paramètres SQL masqués, payloads sûrs | Corps sensibles jamais journalisés |
| Inspection `/proc`, core dump ou swap | Aucun secret autonome durable | Aucun mot de passe en argument ou environnement | `LoadCredential`, `LimitCORE=0`, durée mémoire minimale |
| Substitution entre comptes | Sans objet dans G1 | FK et relation one-to-one | Liaison cryptographique au compte et à la génération |
| Changement du login IMT | Invalide la session HPKE liée à l'ancien login | Ciphertext effacé et reconnexion requise | Ancien credential invalide, réenrôlement requis |
| Rollback d'une sauvegarde | Peut ressusciter une session révoquée | Révocation globale obligatoire avant remise en service | Génération et consentement vérifiés avant déchiffrement |
| Perte de clé privée | Sessions préservées mais worker fermé | Alerte agrégée, restauration de clé ou révocation humaine | Réenrôlement, aucune perte de note |
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

## Invariants G3

- le worker sync possède une identité Unix et un rôle PostgreSQL non privilégié ;
- les quatre clés sont injectées uniquement par `LoadCredential` ;
- le web, scheduler, calendar et outbox ne reçoivent aucune clé privée ;
- le loader accepte uniquement des noms fixes, vérifie fichiers, paires et
  séparation des purposes ;
- les self-tests précèdent tout claim de job ;
- un heartbeat ancien ne satisfait plus la readiness de production ;
- aucune donnée utilisateur, session réelle ou table credential n'utilise HPKE.

## Invariants G4

- le web possède seulement la clé publique des sessions ;
- toute nouvelle session est écrite uniquement en HPKE ;
- le contexte lie compte, login et identifiant de session ;
- révocation, expiration et invalidation effacent les deux formats ;
- la migration legacy est hors réseau, reprenable et ne produit que des
  agrégats ;
- le runtime sync normal ne possède aucun fallback legacy et refuse toute ligne
  ancienne sans la modifier ;
- la clé historique et le module legacy restent accessibles uniquement à la
  commande hors réseau sous le profil explicite `migration`.

## Risques résiduels

Le worker sync normal ne possède plus la clé symétrique générale. Le web
conserve cette clé pour d'autres secrets, mais elle ne permet pas d'ouvrir les
sessions PASS/HUB HPKE.
L'exception locale `owner_managed`, lorsqu'un exploitant l'a volontairement
configurée pour son compte unique, reste également hors du modèle multi-compte
et hors de G1.

Un accès root au LXC, une compromission du binaire du worker sync ou une
inspection de sa mémoire pendant un futur SSO resteront des risques
irréductibles. L'enveloppe asymétrique réduira la surface de déchiffrement, mais
ne rendra jamais l'exploitation d'un mot de passe sans risque.

La présence d'une table vide et d'une primitive isolée ne constitue pas une
capacité autonome. Toute activation avant G5 à G7 doit être considérée comme
une erreur de configuration. G4 est terminé ; G5 à G7 restent fermés.
