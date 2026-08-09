# Runner CI auto-hébergé

Le workflow principal utilise exclusivement le runner repository-scoped
`pve-mini-IMTegrale`, ciblé par les labels `self-hosted`, `linux`, `x64`,
`pve-mini` et `IMTegrale`. Il expose un seul job stable, `ci`, nommé
`CI full`. Aucun fallback vers un runner GitHub-hosted n'est autorisé.

Le YAML est volontairement réduit au pont GitHub nécessaire : attestation du
runner, checkout, sélection des runtimes, appel de l'orchestrateur du dépôt,
upload et téléchargement du même artefact. Le pipeline réel est défini par
`scripts/ci/run_full_pipeline.py` et `scripts/ci/pipeline_spec.json`. Il peut
être lancé depuis un poste compatible, le runner auto-hébergé ou un futur
daemon CI sans recopier les contrôles dans GitHub Actions.

## Un job, six phases

Les six identifiants historiques sont conservés comme phases séquentielles :

1. `content-boundary` — frontière publique, analyse statique, scan de secrets
   et politique du workflow ;
2. `backend` — Ruff, OpenAPI, tests complets avec couverture, planchers,
   construction du wheel et audit Python ;
3. `postgres-backend` — PostgreSQL 17, migrations, contraintes, concurrence,
   rollback et replay ;
4. `frontend` — audit de production, client généré, ESLint, Prettier, Vitest,
   typecheck, build, budgets, Playwright et Axe sans retry ;
5. `release-artifact` — SBOM, manifest, scans, smoke test et digest du wheel et
   du frontend déjà construits ;
6. `release-artifact-roundtrip` — empaquetage du même contenu, extraction sûre,
   comparaison des manifests et digests, scans et smoke test sans
   reconstruction.

Une seule préparation crée l'environnement Python, installe les dépendances
Python et pnpm, vérifie les dépendances système Playwright puis installe le
navigateur. Le frontend, le wheel et le SBOM sont chacun construits une seule
fois. Toute commande non nulle ou toute interruption échoue le pipeline et les
phases non exécutées restent explicitement `not_run` dans le rapport.

Le rapport machine-readable `artifacts/ci-report.json` contient le SHA source,
l'attestation du runner, les horodatages, les six identifiants, statuts,
durées, nombres de contrôles et le digest de l'artefact. Il ne contient ni
variable d'environnement brute, ni secret, ni chemin privé. Le résumé GitHub
présente les mêmes résultats. L'unique artefact `imtegrale-SHA` contient le
rapport et le bundle audité sous `release/`. Après l'upload, le job télécharge
ce même artefact, compare son inventaire exact et réexécute les frontières,
scans et smoke tests sur le téléchargement.

## Isolation et verrou global

La VM CI persistante héberge plusieurs runners repository-scoped. Un verrou
global administré par l'infrastructure autorise un seul job à utiliser la VM à
la fois. Le hook `ACTIONS_RUNNER_HOOK_JOB_STARTED` acquiert ce verrou pendant
`Set up runner`, avant l'initialisation des conteneurs de service par le
runner. Le run historique 141 a confirmé cet ordre : la longue attente dans
`Set up runner` s'est terminée avant `Initialize containers` du job
PostgreSQL. Le service PostgreSQL 17 du job unique est donc créé sous le même
verrou, qui reste acquis jusqu'à la fin de toutes les phases et du round trip.

Le dépôt ne crée aucun second verrou, `flock` ou mutex. Il ne modifie aucun
hook. Le service PostgreSQL utilise uniquement des données de test et aucun
mode privilégié. Le workflow ne lance aucun nettoyage Docker global, ne
redémarre aucun service et n'installe aucun paquet système.

Les caches autorisés restent limités à pip, pnpm, Playwright et aux outils. Le
checkout est reproductible grâce à `clean: true`, `fetch-depth: 1` et
`persist-credentials: false`. L'installation Playwright n'utilise pas
`--with-deps` : un `install-deps --dry-run` vérifie d'abord l'hôte, puis seul
Chromium est installé ou repris depuis son cache. Une dépendance système
manquante constitue un blocage d'infrastructure.

## Déclenchement et frontière d'administration

Le workflow accepte les pushes sur `main`, les pull requests et le lancement
manuel. Le job est filtré avant attribution du runner lorsqu'une pull request
est draft. Il s'exécute lors du passage `ready for review`, et sur
`synchronize` ou `reopened` seulement si la pull request n'est pas draft. La
concurrency annule l'ancien SHA lorsqu'un commit plus récent est poussé.

Le runner n'a accès ni au PVE ou à son API, ni aux LXC, VM ou environnements de
production, ni aux credentials de production, ni à un autre dépôt. La CI
n'utilise que des données et services de test et n'autorise aucun déploiement.

Si le runner est hors ligne, le job reste en file sans fallback. L'incident
doit être traité côté infrastructure : laisser le job courant se terminer,
contrôler le service du runner, Docker et les hooks, puis remettre le runner en
ligne et vérifier son nom et ses labels via GitHub. Cette maintenance ne
modifie ni les workflows métier, ni les contrôles, ni les permissions GitHub.
