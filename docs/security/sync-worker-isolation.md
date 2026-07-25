# Isolation du worker de synchronisation

## Portée G3 et G4A

G3 crée la frontière d'exécution dédiée. G4A y branche uniquement les sessions
techniques PASS/HUB :

- toute nouvelle session PASS/HUB est scellée en HPKE ;
- les anciennes sessions sont migrées hors réseau ;
- aucun mot de passe multi-compte n'est conservé ;
- `imt_sync_credentials` reste vide ;
- `autonomous` reste indisponible ;
- la paire credential reste limitée au self-test synthétique.

## Identités et accès

Le service `botnote-sync-worker.service` s'exécute comme l'utilisateur système
`botnote-sync`, sans home, shell interactif, mot de passe, sudo ou capacité
Linux. Deux groupes secondaires bornés sont utilisés :

- `botnote-runtime` donne uniquement accès en lecture aux releases et venvs ;
- `botnote-sync-lock` donne accès au répertoire de verrous partagé.

L'utilisateur n'appartient pas au groupe privé général `botnote`. Il ne peut
donc pas lire directement `/etc/botnote/botnote.env`, les clés TLS, Parcours,
les secrets d'administration ou les sauvegardes. Le fichier privé
`/etc/botnote/botnote-sync.env` est `root:root 0600` et est lu par systemd avant
la baisse de privilèges. Pendant G4A, il contient temporairement la clé
symétrique nécessaire à la lecture legacy et le pepper requis par le flux sync,
mais aucune clé HPKE, donnée Telegram ou configuration Parcours. G4B retire la
clé symétrique du profil normal.

Le rôle PostgreSQL `botnote-sync` utilise le socket Unix local et
l'authentification `peer`. Son nom correspond exactement à l'identité Unix :
aucun mot de passe de base n'est placé dans l'environnement. Le profil sync
refuse en production toute URL différente de
`postgresql+psycopg:///botnote`, notamment une URL contenant un hôte, un
utilisateur, un mot de passe ou une option de socket. Le rôle est
`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION` et
`NOBYPASSRLS`. Il reçoit seulement `CONNECT`, `USAGE` du schéma et le DML sur
les tables nécessaires aux jobs sync, comptes, résultats, sessions, opérations,
événements, cohortes, outbox et heartbeats. Il ne possède ni DDL, ni migration,
ni table d'administration, ni `imt_sync_credentials`.

Les deux lignes de `deploy/security/botnote-sync.pg-hba.conf` sont installées
avant la règle Debian générique `local all all peer` : la première autorise
uniquement la base `botnote`, la seconde refuse explicitement cette identité sur
les autres bases. `pg_hba_file_rules` doit être sans erreur avant le reload.

Le provisionnement est explicite et idempotent :

```bash
/opt/botnote/releases/RELEASE/deploy/security/provision-sync-runtime
sudo -u postgres psql --dbname botnote \
  --file /opt/botnote/releases/RELEASE/deploy/security/provision-sync-postgres-role.sql
```

## Verrou partagé

Tous les chemins manuel, automatique, administratif et CLI conservent le même
`BOTNOTE_SYNC_LOCK_DIR=/run/botnote-sync-locks`. `tmpfiles.d` crée :

- le dossier `root:botnote-sync-lock 2770` ;
- `.creation.lock` en `0660`.

Le lock de création sérialise la création des fichiers par compte. Le processus
créateur fixe leur mode à `0660`; les ouvertures suivantes valident ce mode sans
tenter un `chmod` réservé au propriétaire du fichier. Les fichiers sont ouverts
relativement à un descripteur de dossier avec `O_NOFOLLOW`, puis verrouillés par
`flock`. Le groupe setgid garde une identité de groupe commune sans rendre le
répertoire world-writable.

Avant la première bascule, le provisionneur réattribue les anciens fichiers de
lock au groupe dédié. Il ne doit être exécuté que dans la fenêtre sans job sync
actif décrite ci-dessous.

## Clés opérationnelles

Le provisionneur `deploy/security/provision-sync-hpke-keys` crée exactement deux
paires X25519 distinctes :

- `imt-sync-credential` ;
- `pass-service-session`.

Usage initial :

```bash
/opt/botnote/runtime/bin/python \
  /opt/botnote/releases/RELEASE/deploy/security/provision-sync-hpke-keys provision
/opt/botnote/runtime/bin/python \
  /opt/botnote/releases/RELEASE/deploy/security/provision-sync-hpke-keys verify
```

Le répertoire `/etc/botnote/sync-hpke` est `root:root 0700`. Les quatre fichiers
raw et `keyset.json` sont `root:root 0400`. Le provisionnement est atomique,
refuse une cible existante, les symlinks, hardlinks, types spéciaux, tailles
incorrectes et paires incohérentes. `verify` n'affiche que le nombre de purposes
et le fait que les paires sont distinctes, jamais une clé ou un ciphertext.

Ces fichiers ne font partie ni du dépôt, ni du wheel, ni de l'artefact CI, ni du
SBOM, ni des sauvegardes PostgreSQL. Les sessions PASS/HUB utilisent désormais
la paire session. Sa perte exige soit une restauration hors machine, soit la
révocation confirmée de toutes les sessions et une reconnexion ; aucune note
n'est perdue.

## Credentials systemd

L'unité dédiée utilise `LoadCredential=` avec exactement quatre noms logiques :

- `imt-sync-credential-private` ;
- `imt-sync-credential-public` ;
- `pass-service-session-private` ;
- `pass-service-session-public`.

L'unité web reçoit séparément et exclusivement
`pass-service-session-public`. Elle ne reçoit ni clé privée ni paire credential.
Le loader web refuse tout autre nom et réalise un scellement synthétique avant
l'écoute.

Le code lit uniquement le dossier absolu indiqué par
`CREDENTIALS_DIRECTORY`. Il n'accepte aucun chemin de clé, fallback cwd, home,
`/tmp`, variable brute ou valeur PostgreSQL. Le loader refuse les fichiers
supplémentaires, sauf le credential privé facultatif
`owner-imt-password`. Chaque clé doit être un fichier régulier sans hardlink,
strictement privé et de 32 octets. Les paires doivent correspondre et être
distinctes.

Une exception propriétaire historique peut être conservée temporairement avec
un drop-in privé :

```ini
[Service]
LoadCredential=owner-imt-password:/chemin/source/prive
Environment=BOTNOTE_OWNER_IMT_PASSWORD_FILE=%d/owner-imt-password
```

Le chemin réel et l'identifiant restent hors Git. Le fichier source historique
n'est pas supprimé pendant la fenêtre de rollback G2.

Après copie par PID 1, le namespace du service masque le répertoire source,
`botnote.env`, mTLS, Parcours, l'administration et les sauvegardes. Les clés ne
figurent jamais dans `Environment=`, `EnvironmentFile=`, `ExecStart=`, les
arguments ou PostgreSQL.

## Démarrage fermé et supervision

La seule commande sync durable autorisée est :

```text
botnote sync-worker
```

`botnote worker sync` est refusé par argparse et l'instance générique possède
également un `ExecCondition` fermé. La commande dédiée suit cet ordre :

1. configurer les logs structurés ;
2. valider le profil de configuration `sync` et l'identité `botnote-sync` ;
3. charger les quatre credentials ;
4. vérifier les paires ;
5. exécuter un round-trip credential fictif ;
6. exécuter un round-trip session fictif ;
7. seulement ensuite appeler `run_worker("sync")`.

Les self-tests restent en mémoire et n'ouvrent ni base, ni job, ni réseau. Une
clé absente, invalide, incohérente ou un self-test échoué termine le processus
avec un code stable et sans secret.

Le heartbeat G4A conserve temporairement :

```text
runtime_profile=isolated-sync-v1
hpke_credentials_ready=true
hpke_purposes=2
dedicated_identity=true
```

En production, `/health/ready` devient non vert si le heartbeat est absent,
ancien ou non isolé. `/health/live` reste vert tant que le web fonctionne.
`operations-check` ajoute des codes agrégés
`SYNC_WORKER_NOT_ISOLATED` ou `SYNC_HPKE_KEYS_NOT_READY`.

## Durcissement systemd

L'unité applique notamment `NoNewPrivileges`, `PrivateDevices`, `PrivateTmp`,
`PrivateMounts`, `ProtectSystem=strict`, `ProtectProc=invisible`,
`ProcSubset=pid`, `MemoryDenyWriteExecute`, `MemorySwapMax=0`, `LimitCORE=0`,
`RestrictNamespaces`, un filtre `@system-service`, une bounding set vide et les
familles d'adresses limitées à `AF_INET`, `AF_INET6` et `AF_UNIX`.

Toujours valider sur la cible :

```bash
systemd-analyze verify /etc/systemd/system/botnote-sync-worker.service
systemd-analyze security botnote-sync-worker.service
systemctl show botnote-sync-worker.service \
  -p User -p Group -p SupplementaryGroups -p LimitCORE \
  -p MemorySwapMax -p ProtectProc -p ProcSubset -p PrivateMounts
```

Les résultats doivent être lus sans afficher les environnements ou le contenu
de `CREDENTIALS_DIRECTORY`.

## Migration G4A sans appel PASS

1. Vérifier l'artefact CI retéléchargé et son manifeste.
2. Tester la sauvegarde PostgreSQL dans une base isolée.
3. Vérifier Alembic `0025`, zéro credential, zéro compte autonome, aucun job ou
   lease sync actif, et aucune échéance immédiate.
4. Provisionner identités, groupes, locks, rôle PostgreSQL, sync env et clés.
5. Installer l'unité et son éventuel drop-in propriétaire, puis vérifier
   systemd.
6. Arrêter le scheduler et attendre tous les leases.
7. Arrêter et désactiver `botnote-job-worker@sync.service`.
8. Appliquer `0026`, basculer G4A et démarrer le web avec la seule clé publique.
9. Exécuter `pass-sessions-migrate-hpke --dry-run`, puis la migration réelle ;
   legacy, mixed et échecs doivent être à zéro.
10. Démarrer `botnote-sync-worker.service` et vérifier UID, self-tests et
   heartbeat, sans ajouter de job.
11. Démarrer calendar et outbox, puis le scheduler uniquement dans une fenêtre
    sans synchronisation immédiatement échue.
12. Vérifier live, ready, operations-check, zéro appel PASS/HUB/COMPETENCES,
    zéro notification, zéro credential et zéro compte autonome.

## Rollback G4A pendant la contraction G4B

1. Arrêter le scheduler et attendre les leases.
2. Arrêter l'unité sync G4B.
3. Restaurer le fichier privé G4A contenant temporairement la clé legacy.
4. Rebasculer ensemble `current` et `runtime` vers G4A.
5. Démarrer le worker G4A puis le scheduler.
6. Vérifier les enveloppes et Alembic `0026`.

Ne pas downgrader la base et ne jamais réécrire une enveloppe HPKE en legacy.

## Risques résiduels

- Le worker sync G4A charge encore temporairement la clé symétrique générale ;
  G4B doit la retirer.
- Le web peut sceller une session mais ne peut pas l'ouvrir.
- L'exception propriétaire historique reste compatible avec l'ancien runtime.
- Root dans le LXC compromet toutes les clés.
- Une RCE dans le worker sync compromet les futures clés privées.
- Les sessions PASS/HUB sont protégées par HPKE ; aucun mot de passe ne l'est.
- Rotation et sauvegarde hors machine des clés ne sont pas encore livrées.
- `autonomous` reste indisponible.
