# Release IMTégrale

La production utilise deux liens atomiques :

- `/opt/botnote/current` vers le code et le frontend d'une release ;
- `/opt/botnote/runtime` vers son environnement Python immuable.

## Préparation

1. Exécuter les tests backend, les tests frontend, le typecheck, le build, le scan de secrets, les audits de dépendances et l'audit de l'artefact. La CI construit le wheel et le frontend depuis les locks, génère un SBOM CycloneDX, contrôle leurs frontières, produit un manifeste SHA-256 puis exécute le smoke-test sur le wheel installé.
2. Copier `deploy/network.env.example` vers `/etc/default/botnote-network` sur le PVE, remplacer toutes les valeurs d'exemple, puis installer ce fichier en `root:root 0600`. Les configurations Nginx, dnsmasq, Proxmox et nftables du dépôt décrivent le même réseau d'exemple : les rendre avec ces valeurs avant installation et contrôler le diff produit.
3. Créer un dump PostgreSQL chiffré et un `vzdump` du conteneur, puis tester l'archive Zstandard. Pour un lancement manuel depuis une session administrative durcie, exécuter `umask 022; vzdump ...` dans un sous-shell : le `umask 027` interactif empêcherait sinon l'utilisateur mappé du conteneur non privilégié de traverser le répertoire temporaire.
4. Construire un wheel avec `pip wheel --no-deps` dans l'environnement de build isolé et fixé par `pyproject.toml`, depuis un arbre sans ancien dossier `build/`, puis exécuter `scripts/audit_release.py` sur le wheel, le frontend et le SBOM. `setuptools` peut sinon conserver dans le wheel un module supprimé mais encore présent dans `build/lib`.
5. Créer l'archive avec `COPYFILE_DISABLE=1` et `tar --no-xattrs`; exclure `build`, `dist`, `*.egg-info`, `__pycache__`, `*.pyc`, les tests, `node_modules` et les caches.
6. Vérifier les SHA-256 après chaque transfert.

La révision `0017` supprime physiquement les anciennes colonnes de mot de passe IMT. Si l'opérateur choisit l'exception facultative pour son unique compte propriétaire, il doit créer **avant** la migration `/etc/botnote/owner-imt-password`, sans passer le secret dans l'historique du shell, puis l'installer en `botnote:botnote 0400`. Définir ensuite `BOTNOTE_OWNER_IMT_USERNAME` et `BOTNOTE_OWNER_IMT_PASSWORD_FILE` dans l'environnement privé. Aucun autre compte ne doit disposer d'un secret local.

## Installation LXC

1. Extraire dans `/opt/botnote/releases/<release>` avec `--no-same-owner`.
2. Installer `age`, déposer uniquement la clé publique de sauvegarde dans `/etc/botnote/backup-age-recipient` en `root:botnote 0640`, et conserver la clé privée de restauration hors du PVE et du LXC. Installer `deploy/backup.sh` en `/usr/local/libexec/botnote-backup`, propriétaire `root:root` et mode `0755` ; l'unité ne dépend ainsi jamais du contenu d'une release applicative. Un dump n'est valide qu'après restauration testée depuis son fichier `.dump.age` sur une base isolée.
3. Créer un environnement neuf avec `python3 -m venv /opt/botnote/venvs/<release>` ; ne jamais recopier un ancien venv, car ses scripts contiennent des chemins absolus. Installer `deploy/requirements.lock`, puis le wheel IMTégrale avec `python -m pip install --no-deps`. Vérifier que les shebangs de `bin/botnote` et `bin/alembic` pointent vers le nouveau chemin, puis appliquer `chown -R root:botnote` et `chmod -R g+rX,o-rwx` à la release et au venv. Le groupe `botnote` doit pouvoir lire les fichiers et traverser tous les répertoires, y compris la racine extraite de l'archive. Ne pas compter sur l'`umask` seul : une archive tar conserve ses propres modes.
4. Installer une copie adaptée de `botnote-runtime.env` en `root:botnote 0640`; `BOTNOTE_BIND_HOST` doit être l'adresse privée du conteneur et `BOTNOTE_TRUSTED_PROXY_IPS` ne doit contenir que le frontal. Les secrets et surcharges privées restent exclusivement dans `botnote.env` ou dans le fichier propriétaire dédié. Les unités systemd et `botnote-cli` chargent volontairement `botnote-runtime.env` en premier puis `botnote.env`, afin qu'une restriction privée ne puisse pas être réécrasée par les valeurs génériques du runtime. En production, les clés de chiffrement doivent être des valeurs base64 URL-safe de 32 octets, les peppers doivent contenir au moins 32 octets et toutes ces valeurs doivent être distinctes. `BOTNOTE_PASS_SESSION_MAX_DAYS` ne doit jamais dépasser 30. Pour Parcours, conserver `BOTNOTE_LEARNING_CONTENT_ROOT=/opt/botnote-learning` et `BOTNOTE_LEARNING_STUDENT_STATUS_MAX_AGE_DAYS=30`, ou réduire cette dernière durée après analyse d'impact. Cette fraîcheur est indépendante de la session : seule une authentification IMT réussie la renouvelle, jamais une passkey, un token ou une consultation. Le mode `cohort` conserve le comportement FIP 2028 existant. Une release personnelle doit sélectionner explicitement `BOTNOTE_LEARNING_ACCESS_MODE=personal` dans le fichier privé et renseigner une audience distincte préfixée `personal:`, l'allowlist de logins IMT et l'allowlist réseau exactes décrites plus bas ; une liste absente ou vide, ou l'audience générale `fip:2028`, fait échouer la configuration.
5. Définir `BOTNOTE_ADMIN_ALLOWED_IDENTITIES` dans `/etc/botnote/botnote.env`. Une liste vide garde toutes les routes admin invisibles.
6. Valider les unités avec `systemd-analyze verify`, nftables avec `nft -c -f`, puis exécuter `alembic upgrade head` sous l'utilisateur `botnote`.
7. Installer `botnote-web.service`, `botnote-scheduler.service`, `botnote-job-worker@.service`, `botnote-operations-check.service`, `botnote-operations-check.timer` et le CLI, puis exécuter `systemctl daemon-reload`. Arrêter et désactiver `botnote-worker.timer` ainsi que `botnote-worker.service`; ne jamais créer `/etc/botnote/enable-legacy-worker` hors rollback vers une ancienne release. Après `alembic upgrade head`, activer `botnote-scheduler.service`, les instances `botnote-job-worker@sync.service`, `botnote-job-worker@calendar.service`, `botnote-job-worker@outbox.service` et le timer de contrôle opérationnel.
8. Basculer `current` et `runtime`, puis redémarrer ensemble l'API, l'ordonnanceur et les trois workers. Attendre leur premier heartbeat avant de tester `/health/ready`; ce healthcheck contrôle PostgreSQL, la révision Alembic et la fraîcheur interne, sans appeler PASS. Charger nftables et redémarrer Nginx. Vérifier que les comptes automatiques sans session sont en pause `reauth_required` et que l'état PASS est disponible sans déclencher de synchronisation réelle. Le nouveau consentement leaderboard inclut la date de vérification et la fraîcheur publique ; les anciens participants doivent donc consentir de nouveau.
9. Créer le répertoire dédié avec `install -d -o botnote -g botnote -m 0700 /var/lib/botnote-admin`, puis amorcer le premier compte avec `botnote admin-bootstrap --username <nom> --output /var/lib/botnote-admin/initial-credentials.txt`. Le fichier doit rester `0600`, être supprimé après lecture et le mot de passe doit être changé à la première connexion. La première session permet ensuite d'enrôler une passkey pendant dix minutes. Après cet enrôlement, toute nouvelle session admin exige cette passkey et les mutations sensibles exigent un step-up de moins de dix minutes. Conserver au moins deux passkeys administrateur sur des dispositifs distincts ; la dernière ne peut pas être supprimée depuis l'interface. Ne pas relâcher les permissions du répertoire legacy `/var/lib/botnote`.

Un redémarrage Nginx complet est volontaire lors d'un changement d'upstream : un reload gracieux peut conserver un ancien worker tant qu'une connexion SSE reste ouverte.

### Rotation des secrets applicatifs

La rotation des clés de chiffrement et peppers suit la procédure détaillée dans [`docs/security/key-rotation.md`](../docs/security/key-rotation.md). En résumé, installer d'abord la nouvelle valeur active et conserver l'ancienne dans `BOTNOTE_CREDENTIAL_PREVIOUS_KEYS` ou `BOTNOTE_TOKEN_PREVIOUS_PEPPERS`, redémarrer tous les processus, puis lancer :

```bash
botnote keys-reencrypt --dry-run
botnote keys-reencrypt --batch-size 100
botnote keys-reencrypt --dry-run
```

La commande est transactionnelle par lot, reprenable et n'affiche ni secret ni donnée académique. Ne retirer une ancienne clé qu'après un inventaire à zéro, une sauvegarde restaurée et la validation de tous les processus. Pour un pepper, laisser la période de coexistence couvrir la durée maximale des sessions et tokens, ou les révoquer explicitement avant retrait.

## Release privée IMTégrale Parcours

Parcours est une capacité optionnelle de l'application. Le code public contient le chargeur, les schémas, les contrôles d'accès, le renderer générique et des fixtures explicitement fictives. Les documents, métadonnées de droits, catalogues et index réels restent dans le dépôt privé puis dans un volume de production distinct : ils ne sont jamais copiés dans `/opt/botnote/current`, `frontend/public`, `frontend/dist` ou `backend/app/static`.

Le stockage de production suit cette structure :

```text
/opt/botnote-learning/                 root:botnote 0750
├── releases/                          root:botnote 0750
│   ├── demo-fictif-001/               release compilée, immuable
│   └── RELEASE_ID/                    release privée compilée, immuable
└── current -> releases/RELEASE_ID     lien basculé atomiquement
```

`BOTNOTE_LEARNING_CONTENT_ROOT` désigne `/opt/botnote-learning`, pas une release particulière. Le serveur résout uniquement son lien `current`, puis les IDs déclarés dans le manifest. Nginx ne possède ni `root` ni `alias` vers ce dossier : catalogue, sources, PDF, images et téléchargements traversent tous `/api/v1/learning/...` et l'autorisation FastAPI. Les assets acceptent une seule plage HTTP par requête : l'autorisation précède l'ouverture, une plage valide reçoit `206`, une plage invalide `416`, et aucune erreur ne contient de chemin. Les locations dédiées à cette API et aux deep links `/parcours/...` omettent URI, arguments, référent et corps de requête de leurs journaux, neutralisent le journal d'erreur susceptible de recopier l'URI, désactivent le cache et renvoient les headers privés même lorsque Nginx produit lui-même une erreur de limite. Le même format expurgé et la même neutralisation du journal d'erreur sont appliqués au niveau du serveur afin de couvrir les requêtes rejetées pendant le parsing, avant toute sélection de location. Les métriques de statut agrégées remplacent donc les logs d'erreur détaillés sur ce serveur public.

### Portée personnelle optionnelle

Le mode `personal` est une restriction générique de déploiement, pas une donnée du frontend. Il conserve les invariants owner primaire, compte actif, absence de token partagé, méthode IMT/passkey, preuve académique et fraîcheur IMT, puis exige en plus :

- `BOTNOTE_LEARNING_AUDIENCE_ID` identique à l'audience unique du bundle actif et préfixé `personal:` ; l'audience générale `fip:2028` est refusée dans ce mode ;
- exactement un login IMT stable dans `BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES` ; ni le nom affiché ni un nom transmis par React ne sont utilisés ;
- l'identité exacte produite par le frontal dans `BOTNOTE_LEARNING_ALLOWED_IDENTITIES`, exclusivement sous forme `lan:…` ou `tailnet:…`.

Les valeurs réelles restent dans `/etc/botnote/botnote.env`, en `root:botnote 0640`, jamais dans le dépôt ou le manifest public. Pour une identité LAN, réserver l'adresse du terminal dans DHCP avant de déclarer `lan:<adresse>` ; pour Tailnet, utiliser l'identité de connexion exacte fournie par Tailscale Serve. Le frontal classe en `peer:…` toute combinaison de port ou d'en-têtes qu'il ne reconnaît pas ; seul son listener LAN 443 produit `lan:…`. Ne jamais autoriser `internet:…`, `peer:…`, un préfixe, un joker ou un sous-réseau entier. `BOTNOTE_TRUSTED_PROXY_IPS` doit continuer à ne contenir que le frontal mTLS : un header `X-BotNote-Client-Identity` envoyé directement par un autre pair est ignoré.

Le même garde réseau sert les API, assets, téléchargements et deep links `/parcours`. La vue de session masque également le CTA hors ingress. L'allowlist de comptes précède l'accès académique et les grants : un grant temporaire ne peut donc pas ajouter un autre compte en mode personnel. Changer de compte ou d'audience exige une nouvelle configuration privée, un bundle portant exactement cette audience et le redémarrage contrôlé du service.

L'unité web déclare `ReadOnlyPaths=-/opt/botnote-learning`. Le préfixe `-` autorise l'absence du dossier : sans bundle, sans lien `current` ou avec un bundle invalide, l'application principale démarre normalement et seul Parcours répond de manière indisponible. Lorsqu'il existe, le volume reste en lecture seule dans l'espace de montage du service, en plus des permissions Unix.

Le chargeur borne une release v1 ou v2 à un manifest de 32 Mio, un index de recherche de 16 Mio et 10 000 documents, un asset de 512 Mio, et 2 Gio cumulés. Il recalcule intégralement chaque SHA-256 lors de l'activation. Ensuite, une ouverture d'asset vérifie uniquement que le descripteur est toujours un fichier régulier avec un seul lien physique et que son identité immuable enregistrée (`device`, inode, taille, `mtime`, `ctime`) n'a pas changé ; le payload n'est donc pas lu une première fois avant son streaming. Cette optimisation suppose impérativement une release réellement immuable et non modifiable par l'utilisateur du service. Une modification, un remplacement ou un hardlink après validation rend l'asset indisponible, mais un administrateur privilégié capable de falsifier le stockage reste hors de cette frontière de confiance.

La recherche prépare les textes normalisés et les rattachements structurels une fois au chargement, applique les filtres avant le matching, refuse plus de quatre recherches CPU simultanées par processus et conserve la limite par compte. La solution reste un parcours linéaire borné : mesurer latence et RSS avec le pire index synthétique avant d'augmenter l'une de ces limites. Toute augmentation doit être alignée avec le compilateur privé et les tests de charge, sans fallback silencieux.

### Compiler et valider

La compilation s'exécute sur un hôte de build séparé, depuis `IMTegrale-Parcours-Private`. Le service web de production ne reçoit ni dépôt privé, ni compilateur, ni OCR, ni outil de conversion ou d'import. Avant tout transfert :

1. compiler une release dans un nouveau dossier portant un `release_id` stable et unique ;
2. vérifier les droits de chaque source pour chaque audience, résoudre toutes les références et refuser toute entrée inconnue ;
3. produire les SHA-256 après la normalisation finale, puis figer le manifest et le rapport de validation ;
4. exécuter, depuis le dépôt privé, `python tools/validate_release.py <release_dir>` ;
5. exécuter les tests génériques publics, le garde anti-fuite et l'inventaire de l'archive ;
6. transférer uniquement la release compilée et son rapport par un canal administratif protégé, puis revérifier les SHA-256 sur la cible.

Le validateur doit échouer avant installation pour un schéma inconnu, une clé supplémentaire, un checksum faux, un droit incompatible, une référence non résolue, un chemin absolu, une traversée ou un lien symbolique sortant. Le chargement runtime refait les contrôles qui protègent la frontière web ; un rapport de compilation ne remplace jamais cette validation.

Le format v1 reste accepté afin de ne pas interrompre une release active. Pour
compiler une candidate v2, suivre le [guide de migration générique](../docs/presentation-schema-v2.md) : mettre à jour ensemble le manifest et l'index, écrire les champs de présentation sur tous les nœuds concernés, valider les mathématiques, puis recalculer les checksums. Ne jamais convertir le dossier pointé par `current` sur place.

### Installer et basculer

Créer `/opt/botnote-learning` et `releases` avec `root:botnote 0750`. Extraire la candidate dans un nouveau `/opt/botnote-learning/releases/RELEASE_ID` sans conserver les propriétaires de l'archive, puis appliquer récursivement :

- propriétaire et groupe `root:botnote` ;
- mode `0750` pour les dossiers ;
- mode `0640` pour les fichiers réguliers ;
- aucun fichier modifiable par l'utilisateur `botnote` ;
- aucun lien symbolique non déclaré et validé à l'intérieur de la release.

Contrôler le manifest, les checksums, les droits et `readlink -e` avant de rendre la candidate active. La bascule utilise un lien temporaire dans le même système de fichiers, puis un renommage atomique. Avec `demo-fictif-001` comme identifiant exclusivement fictif :

```bash
ln -s releases/demo-fictif-001 /opt/botnote-learning/.current.demo-fictif-001.new
mv -Tf /opt/botnote-learning/.current.demo-fictif-001.new /opt/botnote-learning/current
systemctl restart botnote-web.service
```

Ne jamais modifier une release déjà active. Une correction produit un nouvel ID, de nouveaux checksums et un nouveau rapport. Conserver la cible précédente jusqu'à la fin de la période d'observation.

### Smoke-test sans intégration externe

Le smoke-test authentifié s'exécute d'abord dans un environnement isolé sur l'artefact exact à installer. Il utilise uniquement le catalogue `DÉMO FICTIVE`, un compte synthétique et des sessions générées par les aides de test. Les transports PASS, HUB, INPASS, Telegram et Drive sont remplacés par des doubles qui échouent si un appel réseau est tenté ; l'egress du runner doit aussi être bloqué.

Le scénario vérifie au minimum l'accès, le catalogue, une leçon, un exercice, une source, un asset, la recherche côté serveur et une mutation de progression. Il vérifie aussi qu'un token `owner` est refusé, qu'un compte non éligible ne découvre rien, que les headers privés sont présents et qu'aucun chemin ou nom de fichier privé n'apparaît dans la réponse ou les logs capturés. Aucun véritable compte étudiant ne sert de smoke-test.

Après la bascule, contrôler le healthcheck de l'application principale et l'état systemd sans appeler une intégration académique. Le test de release isolé constitue le smoke authentifié ; il ne faut ni créer de faux compte en production, ni lancer une reconnexion PASS/IMT pour « tester » le déploiement.

### Rollback et sauvegarde privée

Noter la cible exacte de `current` avant la bascule. En cas d'échec, créer un nouveau lien temporaire vers la release précédente déjà validée, le renommer sur `current` avec `mv -Tf`, puis redémarrer `botnote-web.service`. Vérifier à nouveau le healthcheck principal et le smoke isolé. Ne pas supprimer la candidate défaillante avant d'avoir conservé ses checksums, son rapport et les journaux expurgés nécessaires au diagnostic.

Les releases Parcours, rapports de validation et métadonnées de droits sont des sauvegardes privées, distinctes des archives du dépôt public. Les conserver chiffrés avec une clé de restauration hors de l'hôte, en enregistrant l'ID actif et la cible précédente. La progression reste dans PostgreSQL et suit la procédure de dump chiffré existante. Tester périodiquement la restauration du bundle et de la base dans un environnement isolé ; ne jamais restaurer une archive Parcours dans un checkout public, un dossier statique Nginx ou un artefact Vite.

Le test mensuel de base doit être installé sur un hôte de validation distinct, jamais sur le LXC ou le PVE de production. Créer l'utilisateur non privilégié `botnote-restore`, une base jetable portant exactement le nom `botnote_restore_test`, puis installer la clé privée age en `/etc/botnote-restore/age-identity` avec le mode `0400`. Le fichier `/etc/botnote-restore/restore-test.env`, également en `0400`, contient uniquement l'URL de cette base isolée et le chemin de la copie chiffrée. Installer `botnote-restore-test.service` et son timer sur cet hôte. Le script déchiffre directement vers `pg_restore`, refuse tout autre nom de base, vérifie que la révision restaurée est à la tête Alembic et ne crée jamais de dump en clair. Transférer préalablement la sauvegarde `.dump.age` vers cet hôte par le canal administratif ; la clé privée de restauration ne doit pas revenir en production.

## Vérifications

- Le healthcheck de vivacité doit répondre `200`. Le readiness doit répondre `200` après le démarrage des quatre workers et `503` si PostgreSQL, la migration ou un heartbeat interne est périmé ; il ne doit jamais contacter PASS.
- Le PVE doit joindre `8443` avec son certificat client ; le même appel sans certificat doit échouer au handshake.
- `8080` doit être fermé et un accès Tailnet direct à `8443` doit être bloqué.
- La révision Alembic, les volumes de données et `pg_stat_activity` doivent être contrôlés. Restaurer un fichier `.dump.age` dans une base isolée avec la clé privée hors hôte ; aucun `.dump` en clair ne doit rester sur le LXC.
- Les colonnes `encrypted_imt_password` et `credentials_updated_at` ne doivent plus exister. Une reconnexion publique doit créer une seule ligne active dans `pass_service_sessions`, sans valeur de cookie lisible, et sa révocation doit effacer immédiatement le ciphertext.
- `systemctl --failed` doit être vide sur PVE et LXC.
- Depuis le LAN, `/api/v1/admin/auth/session` doit répondre `404`. Depuis l'identité Tailscale autorisée, il doit répondre sans révéler de compte tant que l'authentification admin n'est pas faite.
- Vérifier qu'une session admin obtenue par mot de passe seul ne peut pas ouvrir le portail après l'enrôlement initial, qu'une passkey est exigée et qu'une mutation sensible refuse un step-up vieux de plus de dix minutes avec `ADMIN_STEP_UP_REQUIRED`. Une lecture non destructive doit rester disponible après expiration du step-up.
- Exécuter `botnote keys-reencrypt --dry-run` et vérifier que `remaining` et `calendar_digests_remaining` valent zéro avant de retirer une ancienne clé ou un ancien pepper.
- Vérifier qu'un nouveau participant est visible par un participant actif dès son inscription, mais ne reçoit lui-même ni lignes ni compteur avant l'échéance de 48 heures. Son retrait et l'effacement de ses données doivent rester immédiats ; il peut se réinscrire aussitôt, mais cette activation redémarre les 48 heures avant consultation. Chaque ligne doit publier seulement le rang, l'identité, le score, la date de vérification et la fraîcheur. Un profil vieux de 30 jours reste visible sans rang actif. Ses coefficients doivent provenir de la dernière génération complète d'ECTS officiels COMPETENCES.
- Vérifier qu'une synchronisation avec métadonnées échues importe depuis COMPETENCES les intitulés, semestres, grades, crédits obtenus et crédits alloués, marque leur source officielle et empêche leur modification par l'étudiant. La migration `0011` ajoute ces champs et restaure les valeurs PASS brutes pour les anciennes notes localement modifiées ou masquées.
- Vérifier qu'un propriétaire peut créer indépendamment cinq simulations GPA et cinq simulations de notes, importer ses UE et évaluations, modifier uniquement la copie du scénario, retrouver son autosauvegarde, comparer deux projections et rebaser explicitement une source modifiée. Un token de partage ne doit voir ni route, ni événement, ni contenu de simulation.
- Vérifier qu'un propriétaire peut connecter son lien iCalendar INPASS, que le secret n'est jamais renvoyé par l'API, qu'un token de partage reçoit `403`, et que le cache des cours est conservé si INPASS devient indisponible. Contrôler une actualisation conditionnelle, l'échéance horaire et l'instance `botnote-job-worker@calendar.service` sans déclencher plusieurs requêtes concurrentes.
- Vérifier qu'un propriétaire peut générer un relevé académique complet ou filtré, avec ou sans identité et annexe PASS. Le PDF doit rester sélectionnable, contenir ses liens PASS, COMPETENCES et GitHub, ne jamais être écrit sur le serveur et répondre avec `Cache-Control: no-store`. Un token de partage doit recevoir `403`.
- Vérifier que `botnote-worker.timer` et `botnote-worker.service` sont désactivés, que l'API ne possède aucun thread d'ordonnancement, et que `botnote-scheduler.service` ainsi que les trois instances de worker durable sont `active`.
- Vérifier que `botnote-operations-check.timer` est actif et que sa dernière exécution renvoie uniquement `{"ok":true,"alerts":[]}`. Les codes d'alerte ne contiennent ni compte, ni URI, ni donnée académique.
- Depuis le portail admin privé, contrôler latence et erreurs HTTP, connexions SSE, files, dead-letters, fraîcheur des workers, circuit/quota PASS et résultats iCalendar agrégés. Cet endpoint ne doit jamais être exposé hors de l'ingress administratif.
- Vérifier dans PostgreSQL que les demandes acceptées possèdent un job `queued`, `running` ou `succeeded`, qu'aucun bail `running` n'est expiré durablement et que tout état `dead_letter` déclenche une investigation. Les payloads de jobs ne doivent contenir que des identifiants techniques, jamais un cookie, mot de passe, token Telegram ou contenu académique.
- Vérifier qu'une demande propriétaire acceptée bloque le même compte pendant dix minutes, qu'une répétition avec la même clé est idempotente et qu'un forçage admin exige un motif audité.
- Vérifier l'état du verrou global, le repos de 60 secondes, les budgets `3/h` et `8/24 h`, ainsi que les fenêtres de métriques admin sans lancer de sonde.

## Rollback

Rebasculer ensemble `current` et `runtime` vers la version précédente, puis restaurer les unités compatibles avant de redémarrer l'API et Nginx. Pour une release antérieure à `0020`, arrêter d'abord `botnote-scheduler.service` et toutes les instances `botnote-job-worker@.service`; ne downgrader `0020` qu'après avoir vérifié qu'aucun job accepté n'est encore `queued` ou `running`. La révision `0003` ajoute les données de classement et d'administration ; la révision `0004` ajoute le consentement automatique ; la révision `0005` ajoute les réservations ; la révision `0006` ajoute les passkeys, promotions et protections PASS globales ; la révision `0007` ajoute l'identité officielle PASS et l'état des tests Telegram ; la révision `0008` introduit le verrouillage des ECTS ; la révision `0009` rend ce verrouillage automatique et supprime l'ancienne validation manuelle ; la révision `0010` ajoute l'import des métadonnées COMPETENCES ; la révision `0011` ajoute les détails académiques officiels et rétablit l'immutabilité des notes PASS ; la révision `0012` efface les anciens délais de réinscription au leaderboard ; la révision `0013` ajoute les scénarios GPA privés et leurs instantanés de référence ; la révision `0014` normalise les semestres ingénieur en `S5` à `S10` tout en conservant la valeur COMPETENCES brute ; la révision `0015` ajoute les scénarios privés de notes, leurs UE et leurs évaluations ; la révision `0016` ajoute les abonnements iCalendar chiffrés, les événements mis en cache et le suivi minimal des tentatives ; la révision `0017` supprime irréversiblement les mots de passe IMT stockés et ajoute les sessions PASS/HUB chiffrées limitées à 30 jours ; la révision `0020` ajoute les jobs durables, leurs options d'exécution et l'outbox Telegram ; la révision `0021` ajoute les passkeys administrateur et invalide volontairement les anciennes sessions admin sans preuve de mot de passe datée ; la révision `0022` ajoute les corrélations et heartbeats d'exploitation ; la révision `0023` convertit les quantités académiques en décimaux avec perte volontaire des décimales au-delà de la précision documentée ; la révision `0024` retire six index doublonnant exactement des contraintes uniques et les recrée au downgrade. Ne pas downgrader `0017` en production : restaurer intégralement la sauvegarde chiffrée pré-release. Un rollback applicatif antérieur à `0021` doit révoquer toutes les sessions administrateur ; un rollback vers `2.x` doit arrêter les workers durables avant de réactiver un éventuel timer historique.
