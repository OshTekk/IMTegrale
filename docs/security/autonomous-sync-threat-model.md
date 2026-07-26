# Modèle de menace de la synchronisation autonome

## Portée

Ce document distingue la fondation G1, la primitive HPKE G2, la frontière
worker G3, la migration de sessions G4, la frontière serveur G5 et le runtime
G6. G5 sait sceller un credential dans les tests,
mais son enrôlement est refusé en production et la table y reste vide. Les
seules capacités distantes actives restent les cookies PASS/HUB protégés par
HPKE et décrits dans la politique actuelle.

Les actifs futurs à protéger seront :

- le mot de passe IMT et la capacité de recréer une session CAS ;
- les cookies PASS/HUB ;
- le consentement et la génération du credential ;
- les clés privées de déchiffrement ;
- le compte académique et ses données.

## Frontières de confiance

| Composant | G5 | Cible autonome |
| --- | --- | --- |
| Navigateur | Aucun écran autonome ; l'API de test exige une saisie distincte et un consentement explicite | UX de consentement G7 |
| API web | Peut sceller un credential lorsque le flag non-production est ouvert, sans capacité de lecture | Même frontière avec activation canary |
| PostgreSQL | Enveloppes de sessions ; zéro credential en production | Enveloppes uniquement, jamais de clé privée |
| Scheduler | Planifie `session_only` depuis le booléen historique | Ne reçoit aucune clé privée |
| Worker sync | Ouvre les sessions PASS/HUB et, en runtime G6 explicitement activé, les credentials actifs | Seul processus autorisé à ouvrir un credential |
| Workers calendar/outbox | Aucun accès au mot de passe | Aucun accès à la clé privée |
| systemd | Credentials privés limités à l'unité sync | Même frontière avec rotation |

## Scénarios

| Incident | Effet dans G5 | Contrôle G5 | Exigence avant autonomie |
| --- | --- | --- | --- |
| Lecture de PostgreSQL ou fuite d'un dump | Une enveloppe de test ou future peut être copiée | Clé privée absente de PostgreSQL ; contexte lié au compte | Revérification du mode et de la génération par G6 |
| RCE dans le web | Peut capturer le mot de passe pendant une saisie et produire des enveloppes | Aucune clé privée ni méthode d'ouverture ; production fermée | Le risque pendant la saisie reste irréductible |
| RCE scheduler/calendar/outbox | Ne peut ni ouvrir ni enrôler un credential | Aucun chemin de lecture, aucune clé privée | Frontière inchangée |
| RCE worker sync | Peut ouvrir les credentials actifs lisibles | Rôle PostgreSQL borné, clé isolée, runtime production fermé | Risque critique accepté seulement lors du futur canary |
| Token `owner` volé | Reçoit une vue neutre et ne peut ni enrôler, ni supprimer, ni purger | Propriétaire primaire requis | Frontière inchangée |
| Session web primaire volée | Peut révoquer ou purger ; enrôler exige encore le mot de passe IMT | Origin, CSRF, rate limits et vérification IMT | Step-up récent à étudier séparément |
| Compte administrateur compromis | Ne peut pas créer, lire ou ouvrir un credential | Aucune route admin ; révocation d'urgence par CLI locale | L'admin futur reste limité aux agrégats |
| Accès root au LXC | Contrôle total du runtime et de la mémoire | Hors protection applicative | Risque résiduel non supprimable, durcissement hôte requis |
| Fuite de `/etc/botnote` | Dépend des fichiers obtenus | Clés privées root-only et injectées uniquement au worker | Clé privée credential séparée des secrets généraux |
| Logs, traceback ou télémétrie | Le secret peut exister brièvement en mémoire web de test | `SecretStr`, validation redacted, événements et réponses sans secret | Corps sensibles jamais journalisés |
| Inspection `/proc`, core dump ou swap | Le secret peut exister brièvement pendant l'enrôlement | Aucun mot de passe en argument, environnement, job ou événement | `LimitCORE=0` et durée mémoire minimale |
| Substitution entre comptes | L'ouverture échoue avec un autre contexte | Liaison compte, login, génération et consentement dans `info` | Revérification transactionnelle par G6 |
| Changement du login IMT | Invalide la session HPKE liée à l'ancien login | Ciphertext effacé et reconnexion requise | Ancien credential invalide, réenrôlement requis |
| Rollback d'une sauvegarde | Peut ressusciter une session révoquée | Révocation globale obligatoire avant remise en service | Génération et consentement vérifiés avant déchiffrement |
| Perte de clé privée | Sessions préservées mais worker fermé | Alerte agrégée, restauration de clé ou révocation humaine | Réenrôlement, aucune perte de note |
| Rotation incomplète | Une enveloppe peut rester sur l'ancienne clé | Dry-run, round-trip, inventaire source et vérification cible | Ancienne clé conservée jusqu'à zéro |
| Révocation pendant un job | Une requête déjà envoyée ne peut pas être rappelée | Vérifications avant et après SSO, génération compare-and-swap | Fenêtre résiduelle documentée |

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

## Invariants G5

- l'enrôlement exige propriétaire primaire, Origin, CSRF, trois acquittements
  et vérification du mot de passe IMT ;
- le flag d'enrôlement est fermé par défaut et interdit en production ;
- le web peut sceller mais ne possède ni clé privée, ni méthode d'ouverture ;
- l'appel PASS précède la transaction SQL et ne déclenche aucun import
  académique ;
- credential, session technique et profil vérifié sont commités ensemble ;
- révocation, purge et transitions vers `manual` ou `session_only` effacent
  immédiatement l'enveloppe ;
- viewers et tokens owner ne révèlent pas l'existence d'un credential ;
- le scheduler ne lit que l'état non secret ; le worker peut ouvrir un
  credential uniquement dans le runtime synthétique G6 explicitement activé ;
- la restauration exige une révocation globale hors réseau avant redémarrage.

## Invariants G6

- le gateway essaie toujours la session avant le credential ;
- le scheduler ne lit que l'état non secret du credential ;
- seul le worker sync possède l'opener et les clés privées ;
- mode, consentement, login, génération, enveloppe et leases sont revérifiés
  avant et après l'unique SSO ;
- un mot de passe refusé ou une enveloppe altérée invalide la génération
  utilisée sans toucher un remplacement concurrent ;
- une panne transitoire ou une clé absente conserve l'enveloppe ;
- aucun secret n'entre dans les jobs, événements, métriques ou logs ;
- les deux purposes disposent d'une rotation hors réseau, idempotente et
  vérifiée ;
- runtime et enrôlement restent fermés en production, avec zéro credential et
  zéro compte autonome.

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

Le moteur G6 est prêt mais ne constitue pas une activation produit. Toute
activation avant G7 doit être considérée comme une erreur de configuration.
L'UX, le consentement visible et le canary restent fermés.
