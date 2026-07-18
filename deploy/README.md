# Release IMTégrale

La production utilise deux liens atomiques :

- `/opt/botnote/current` vers le code et le frontend d'une release ;
- `/opt/botnote/runtime` vers son environnement Python immuable.

## Préparation

1. Exécuter les tests backend, les tests frontend, le typecheck, le build et les audits de dépendances.
2. Copier `deploy/network.env.example` vers `/etc/default/botnote-network` sur le PVE, remplacer toutes les valeurs d'exemple, puis installer ce fichier en `root:root 0600`. Les configurations Nginx, dnsmasq, Proxmox et nftables du dépôt décrivent le même réseau d'exemple : les rendre avec ces valeurs avant installation et contrôler le diff produit.
3. Créer un dump PostgreSQL chiffré et un `vzdump` du conteneur, puis tester l'archive Zstandard. Pour un lancement manuel depuis une session administrative durcie, exécuter `umask 022; vzdump ...` dans un sous-shell : le `umask 027` interactif empêcherait sinon l'utilisateur mappé du conteneur non privilégié de traverser le répertoire temporaire.
4. Construire un wheel avec `pip wheel --no-deps --no-build-isolation`.
5. Créer l'archive avec `COPYFILE_DISABLE=1` et `tar --no-xattrs`; exclure `__pycache__`, `*.pyc`, les tests, `node_modules` et les caches.
6. Vérifier les SHA-256 après chaque transfert.

## Installation LXC

1. Extraire dans `/opt/botnote/releases/<release>` avec `--no-same-owner`.
2. Installer `age`, déposer uniquement la clé publique de sauvegarde dans `/etc/botnote/backup-age-recipient` en `root:botnote 0640`, et conserver la clé privée de restauration hors du PVE et du LXC. Un dump n'est valide qu'après restauration testée depuis son fichier `.dump.age` sur une base isolée.
3. Créer un environnement neuf avec `python3 -m venv /opt/botnote/venvs/<release>` ; ne jamais recopier un ancien venv, car ses scripts contiennent des chemins absolus. Installer `deploy/requirements.lock`, puis le wheel IMTégrale avec `python -m pip install --no-deps`. Vérifier que les shebangs de `bin/botnote` et `bin/alembic` pointent vers le nouveau chemin, puis appliquer `chown -R root:botnote` et `chmod -R g+rX,o-rwx` à la release et au venv. Le groupe `botnote` doit pouvoir lire les fichiers et traverser tous les répertoires, y compris la racine extraite de l'archive. Ne pas compter sur l'`umask` seul : une archive tar conserve ses propres modes.
4. Installer une copie adaptée de `botnote-runtime.env` en `root:botnote 0640`; `BOTNOTE_BIND_HOST` doit être l'adresse privée du conteneur et `BOTNOTE_TRUSTED_PROXY_IPS` ne doit contenir que le frontal. Les secrets restent exclusivement dans `botnote.env`.
5. Définir `BOTNOTE_ADMIN_ALLOWED_IDENTITIES` dans `/etc/botnote/botnote.env`. Une liste vide garde toutes les routes admin invisibles.
6. Valider les unités avec `systemd-analyze verify`, nftables avec `nft -c -f`, puis exécuter `alembic upgrade head` sous l'utilisateur `botnote`.
7. Installer les unités et le CLI, désactiver `botnote-worker.timer`, puis basculer `current` et `runtime`. Depuis `3.0.0`, l'API possède l'unique ordonnanceur PASS ; le timer historique ne doit pas être réactivé.
8. Redémarrer l'API, charger nftables et redémarrer Nginx. Vérifier que les consentements existants n'ont pas changé et que l'état PASS est disponible sans déclencher de synchronisation réelle.
9. Créer le répertoire dédié avec `install -d -o botnote -g botnote -m 0700 /var/lib/botnote-admin`, puis amorcer le premier compte avec `botnote admin-bootstrap --username <nom> --output /var/lib/botnote-admin/initial-credentials.txt`. Le fichier doit rester `0600`, être supprimé après lecture et le mot de passe doit être changé à la première connexion. Ne pas relâcher les permissions du répertoire legacy `/var/lib/botnote`.

Un redémarrage Nginx complet est volontaire lors d'un changement d'upstream : un reload gracieux peut conserver un ancien worker tant qu'une connexion SSE reste ouverte.

## Vérifications

- Le healthcheck public doit répondre `200` sur LAN et Tailnet.
- Le PVE doit joindre `8443` avec son certificat client ; le même appel sans certificat doit échouer au handshake.
- `8080` doit être fermé et un accès Tailnet direct à `8443` doit être bloqué.
- La révision Alembic, les volumes de données et `pg_stat_activity` doivent être contrôlés. Restaurer un fichier `.dump.age` dans une base isolée avec la clé privée hors hôte ; aucun `.dump` en clair ne doit rester sur le LXC.
- `systemctl --failed` doit être vide sur PVE et LXC.
- Depuis le LAN, `/api/v1/admin/auth/session` doit répondre `404`. Depuis l'identité Tailscale autorisée, il doit répondre sans révéler de compte tant que l'authentification admin n'est pas faite.
- Vérifier qu'un nouveau participant est visible par un participant actif dès son inscription, mais ne reçoit lui-même ni lignes ni compteur avant l'échéance de 48 heures. Son retrait et l'effacement de ses données doivent rester immédiats ; il peut se réinscrire aussitôt, mais cette activation redémarre les 48 heures avant consultation. Ses coefficients doivent provenir de la dernière génération complète d'ECTS officiels COMPETENCES.
- Vérifier qu'une synchronisation avec métadonnées échues importe depuis COMPETENCES les intitulés, semestres, grades, crédits obtenus et crédits alloués, marque leur source officielle et empêche leur modification par l'étudiant. La migration `0011` ajoute ces champs et restaure les valeurs PASS brutes pour les anciennes notes localement modifiées ou masquées.
- Vérifier qu'un propriétaire peut créer indépendamment cinq simulations GPA et cinq simulations de notes, importer ses UE et évaluations, modifier uniquement la copie du scénario, retrouver son autosauvegarde, comparer deux projections et rebaser explicitement une source modifiée. Un token de partage ne doit voir ni route, ni événement, ni contenu de simulation.
- Vérifier que `botnote-worker.timer` est désactivé et que le thread `botnote-pass-scheduler` appartient au seul processus API.
- Vérifier qu'une demande propriétaire acceptée bloque le même compte pendant dix minutes, qu'une répétition avec la même clé est idempotente et qu'un forçage admin exige un motif audité.
- Vérifier l'état du verrou global, le repos de 60 secondes, les budgets `3/h` et `8/24 h`, ainsi que les fenêtres de métriques admin sans lancer de sonde.

## Rollback

Rebasculer ensemble `current` et `runtime` vers la version précédente, restaurer les unités compatibles, puis redémarrer l'API et Nginx. La révision `0003` ajoute les données de classement et d'administration ; la révision `0004` ajoute le consentement automatique ; la révision `0005` ajoute les réservations ; la révision `0006` ajoute les passkeys, promotions et protections PASS globales ; la révision `0007` ajoute l'identité officielle PASS et l'état des tests Telegram ; la révision `0008` introduit le verrouillage des ECTS ; la révision `0009` rend ce verrouillage automatique et supprime l'ancienne validation manuelle ; la révision `0010` ajoute l'import des métadonnées COMPETENCES ; la révision `0011` ajoute les détails académiques officiels et rétablit l'immutabilité des notes PASS ; la révision `0012` efface les anciens délais de réinscription au leaderboard ; la révision `0013` ajoute les scénarios GPA privés et leurs instantanés de référence ; la révision `0014` normalise les semestres ingénieur en `S5` à `S10` tout en conservant la valeur COMPETENCES brute ; la révision `0015` ajoute les scénarios privés de notes, leurs UE et leurs évaluations. Ne pas downgrader la base en production sans restaurer la sauvegarde chiffrée pré-release. Un rollback applicatif vers `2.x` doit désactiver l'ordonnanceur embarqué avant de réactiver un éventuel timer historique.
