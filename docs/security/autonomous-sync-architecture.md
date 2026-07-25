# Architecture de la synchronisation autonome

## État de cette release

Cette release G4A termine les gates **G1**, **G2** et **G3**, puis réalise
l'expansion et la migration du gate **G4**. G1 introduit le vocabulaire de
domaine, le schéma additif et les gardes. G2 ajoute un
[format d'enveloppe HPKE](hpke-envelope-format.md) générique, versionné et
entièrement isolé. G3 ajoute la
[frontière système du worker sync](sync-worker-isolation.md), ses clés
opérationnelles hors Git et ses self-tests synthétiques. G4A ajoute la migration
`0026`, le framing fixe et la protection HPKE des sessions PASS/HUB. Le web ne
reçoit que leur clé publique ; le worker sync isolé reçoit le keyring privé.
HPKE reste sans lien avec la table de credentials et aucune enveloppe n'est
exposée par une API. La release ne conserve toujours aucun mot de passe IMT et
ne permet aucune reconnexion autonome.

Les modes cibles sont :

| Mode | Disponible | Données techniques | Comportement |
| --- | --- | --- | --- |
| `manual` | Oui | Session PASS/HUB facultative | Aucun travail planifié |
| `session_only` | Oui | Cookies PASS/HUB chiffrés existants | Planifié tant que la session distante reste valide |
| `autonomous` | Non | Futur credential sous enveloppe | Refusé avant toute mutation dans cette release |

`BOTNOTE_AUTONOMOUS_SYNC_ENABLED` vaut `false` par défaut. Le positionner à
`true` fait échouer la validation de configuration : aucun opérateur ne peut
activer accidentellement un runtime incomplet.

## Expansion compatible avec le rollback

La migration `0025` suit une stratégie expand/contract :

1. `accounts.auto_sync_enabled` est conservé sans changement ;
2. `accounts.auto_sync_mode` est ajouté et initialisé depuis le booléen ;
3. la nouvelle application écrit les deux champs ensemble ;
4. le mode effectif reste dérivé du booléen tant que l'ancienne release est une
   cible de rollback ;
5. aucune contrainte SQL croisée ne force encore leur égalité.

Le mapping initial est fermé :

```text
auto_sync_enabled = false -> manual
auto_sync_enabled = true  -> session_only
```

Une ancienne application peut donc modifier uniquement le booléen sur une base
migrée. Au retour de la nouvelle application, le helper
`effective_sync_mode()` retrouve le comportement correct à partir de ce
booléen, même si la colonne miroir est temporairement désynchronisée.

La future phase de contraction devra être une migration distincte, après
fermeture explicite de cette fenêtre de rollback :

1. interdire tout rollback vers une release ne connaissant pas le mode ;
2. réconcilier toutes les lignes depuis l'autorité choisie ;
3. rendre `auto_sync_mode` autoritatif ;
4. adapter scheduler et workers ;
5. seulement ensuite supprimer `auto_sync_enabled`.

Un rollback applicatif depuis cette release ne nécessite donc pas de downgrade
de la base. Un downgrade de production reste une opération d'incident séparée.

## Contrat applicatif actuel

Toutes les transitions de la nouvelle application passent par
`services/sync_preferences.py`.

```text
manual
  -> auto_sync_enabled = false
  -> auto_sync_mode = manual

session_only
  -> auto_sync_enabled = true
  -> auto_sync_mode = session_only
```

Le service conserve les règles existantes de consentement, fréquence,
adaptation, prochaine échéance, pause `reauth_required`, timestamps et
événements. Il n'ajoute ni quota, ni retry, ni appel distant.

`PATCH /api/v1/settings/sync-mode` exige une session propriétaire primaire,
Origin et CSRF. Les routes historiques restent compatibles et appellent le même
service :

- `PATCH /api/v1/settings/auto-sync` ;
- `PUT /api/v1/settings/sync-setup`.

Une demande `autonomous` reçoit `409 AUTONOMOUS_SYNC_UNAVAILABLE` avant toute
mutation. Aucun consentement, événement d'activation, job ou credential n'est
créé.

La lecture expose le mode effectif, les deux modes disponibles et l'état
autonome fermé. Elle n'expose aucune donnée cryptographique.

## Table de préparation

`imt_sync_credentials` est une table one-to-one séparée de `accounts`. Elle est
créée vide et aucune route de cette release ne peut y écrire.

Elle prépare les métadonnées minimales d'un futur credential :

- enveloppe binaire versionnée et bornée ;
- `key_id` ASCII borné ;
- génération du credential ;
- état `active`, `invalid` ou `revoked` ;
- version et date de consentement ;
- dates d'utilisation, succès et échec ;
- compteur d'échecs ;
- révocation avec motif en allowlist.

Les contraintes imposent notamment :

- au plus une ligne par compte ;
- suppression en cascade avec le compte ;
- génération et consentement strictement positifs ;
- compteur d'échecs positif ou nul ;
- enveloppe présente seulement pour l'état actif ;
- enveloppe absente pour un état invalide ou révoqué ;
- aucune colonne de mot de passe, hash, empreinte, longueur ou métadonnée libre.

La table n'est pas un stockage utilisable tant que les gates suivants ne sont
pas terminés.

## Défense du scheduler et du worker

Pendant l'expansion, le booléen reste l'autorité de planification. Une seconde
barrière refuse cependant toute ligne dont le mode stocké est inconnu ou
`autonomous`.

Si une modification SQL manuelle injecte ce mode :

1. le scheduler l'écarte et produit seulement une alerte agrégée ;
2. aucun job automatique n'est réservé ;
3. le worker revérifie le compte avant tout appel distant ;
4. aucun déchiffrement ou fallback n'existe.

Le système ne convertit pas silencieusement cette incohérence en
`session_only`.

## Primitive G2 et branchement G4A

Le paquet `app.crypto` utilise directement l'API one-shot HPKE de
`cryptography 49` avec X25519, HKDF-SHA-256 et ChaCha20-Poly1305. Il fournit :

- deux purposes cryptographiquement séparés ;
- des contextes immuables liés par `info` ;
- une enveloppe binaire v1 fermée ;
- un `key_id` SHA-256 complet ;
- un keyring préparé pour la rotation ;
- un frame credential fixe de 3 072 octets ;
- un frame session fixe de 65 552 octets pour préserver la limite historique
  de 64 Kio ;
- une enveloppe session de taille exacte 65 652 octets.

Les tests emploient uniquement des clés et secrets fictifs en mémoire. Le
smoke-test du wheel et le démarrage du worker sync effectuent des round-trips
isolés, sans appel réseau. Le seul chemin applicatif chargeant les clés privées
est `sync-worker -> loader -> SyncRuntimeContext`.

## Frontière G3 livrée

Deux paires X25519 distinctes existent hors Git sous un répertoire root-only.
systemd les copie sous quatre noms fixes uniquement dans
`CREDENTIALS_DIRECTORY` de l'unité `botnote-sync-worker.service`. Le worker
possède une identité Unix, un environnement et un rôle PostgreSQL dédiés.
Le web reçoit uniquement `pass-service-session-public`. Scheduler, calendar,
outbox et le web ne reçoivent aucune clé privée.

La séparation cible devra garantir :

- aucune clé privée dans PostgreSQL, le wheel, Git ou l'environnement général ;
- aucune clé privée dans le web, le scheduler, calendar ou outbox ;
- credential privé fourni uniquement au worker sync par systemd ;
- liaison cryptographique au compte, login normalisé, génération et
  consentement ;
- démarrage fermé du worker lorsque sa configuration est requise mais invalide ;
- aucun mot de passe dans les jobs, événements, métriques ou journaux.

G4A écrit toute nouvelle session PASS/HUB uniquement en HPKE. Le profil
`sync-migration` et le worker G4A conservent temporairement une lecture legacy
isolée afin de migrer une ligne avant son usage. La contraction G4B retire ce
fallback et la clé symétrique du worker normal après inventaire legacy à zéro.

L'exception locale historique `owner_managed` reste un secret hors base limité à
l'unique compte propriétaire d'une instance auto-hébergée. G1 ne la modifie pas,
ne la transforme pas en credential et ne l'associe pas au mode
`autonomous`. Elle ne doit jamais être généralisée aux comptes publics.

## Gates obligatoires

| Gate | Objet | État |
| --- | --- | --- |
| G1 | Schéma, modes, API compatible et gardes fermés | Terminé |
| G2 | Module HPKE versionné avec clés entièrement fictives | Terminé |
| G3 | Worker sync dédié et clé privée isolée | Terminé |
| G4 | Cookies PASS/HUB migrés vers l'isolation worker-only | G4A terminé, contraction G4B requise |
| G5 | API d'enrôlement, renouvellement et suppression | Non terminé |
| G6 | Fallback autonome, révocation et rotation | Non terminé |
| G7 | UX, consentement distinct et activation canary | Non terminé |

`autonomous` reste indisponible tant que G4 à G7 ne sont pas validés. Chaque
gate doit conserver un rollback documenté et employer uniquement des secrets
fictifs en test.
