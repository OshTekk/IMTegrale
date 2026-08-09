# Runner CI auto-hébergé

Le workflow principal utilise exclusivement le runner repository-scoped
`pve-mini-IMTegrale`, ciblé par les labels `self-hosted`, `linux`, `x64`,
`pve-mini` et `IMTegrale`. Il conserve les six jobs historiques, leurs
dépendances et leurs contrôles. Aucun fallback vers un runner GitHub-hosted
n'est autorisé.

## Isolation et verrou global

La VM CI persistante héberge trois runners repository-scoped. Un verrou global
administré par l'infrastructure autorise un seul job à utiliser la VM à la
fois. Le hook de début de job acquiert ce verrou avant la préparation des
conteneurs par le runner GitHub. Il couvre donc la création du service
PostgreSQL, la publication du port `5432`, les autres usages Docker, les
processus de job et les caches partagés. Le dépôt ne crée aucun second verrou,
`flock` ou mutex.

Des hooks administrés nettoient avant et après chaque job les workspaces,
processus et ressources de test. Un nettoyage d'inactivité complète ce cycle.
Les caches d'outils, de paquets et de navigateurs Playwright ainsi que les
images Docker récentes sont volontairement conservés. Le checkout reste
reproductible grâce à `clean: true`, `fetch-depth: 1` et
`persist-credentials: false`. Les environnements Python de développement sont
renouvelés dans les contraintes du projet afin de reproduire la résolution
d'un runner neuf sans modifier les spécifications de dépendances.

Le workflow ne lance aucun nettoyage Docker global, ne redémarre aucun service
et n'installe aucun paquet système. L'installation Playwright sans
`--with-deps` réutilise le navigateur en cache ou télécharge uniquement le
navigateur demandé. Une dépendance système manquante constitue un blocage
d'infrastructure, pas une autorisation d'utiliser `sudo` dans le workflow.

## Frontière d'administration

Le runner n'a accès ni au PVE ou à son API, ni aux LXC, VM ou environnements de
production, ni aux credentials de production, ni à un autre dépôt. La CI
n'utilise que des données et services de test et n'autorise aucun déploiement.

Si le runner est hors ligne, les jobs restent en file et ne basculent pas vers
un autre runner. L'incident doit être traité côté infrastructure : laisser le
job courant se terminer, contrôler le service du runner, Docker et les hooks,
puis remettre le runner en ligne et vérifier son nom et ses labels via GitHub.
Cette maintenance ne modifie ni les workflows métier, ni les timeouts de test,
ni les permissions GitHub.
