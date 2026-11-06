# W10 — préparation locale d'un seul essai W9

État : **NON EXÉCUTÉ, NON PUBLIÉ, AUTORISATION DISTINCTE REQUISE**.
Aucune fenêtre de maintenance certifiée. Ce dépôt neuf contient exclusivement
le candidat W9 exact, un workflow et ce document de qualification synthétique.

## Identité et frontière de publication

`w9_linux_window.py` doit conserver exactement ce SHA-256 :

```text
db7256bb9815c549bdc5364311d99b4c07f7f296dbd53e027725c39216d18366
```

La revue intégrale du candidat ne relève ni secret, credential réel, donnée
personnelle, adresse ou topologie d'infrastructure, incident opérationnel ou
procédure interne déployée. Ses imports sont exclusivement Python stdlib :
argparse, os, pathlib, signal, subprocess, sys, threading et time.
Il exige les binaires Linux systemd et `/usr/bin/python3`, sans installation,
source privée, base de données ou accès réseau applicatif. Les valeurs de
credentials sont littéralement `old` et `new`. Les noms des cinq rôles sont
ceux du laboratoire, sans mapping vers une installation.

Le rapport interne mentionné par le docstring du candidat est volontairement
absent. Aucun rapport opérationnel, test antérieur, historique Git privé ou
autre source privée n'est transporté. Cette revue classe les trois fichiers
proposés comme publiables ; elle ne donne pas l'autorisation de les publier.
L'identité Git du nouveau commit est une identité technique synthétique.

## Une seule voie future, à autoriser

Destination unique envisagée : dépôt public `OshTekk/IMTegrale`, nouvelle branche
`codex/w10-maintenance-window`, jamais la branche principale. Le workflow ne
réagit qu'aux pushes de cette branche ; le job exige en plus le dépôt exact,
une création de branche sans force ni suppression, et `run_attempt == 1`.
Une branche déjà existante interdit cette procédure : pas d'écrasement,
suppression/recréation, modification du candidat ou relance automatique.

Un job, aucune matrice, un seul appel du candidat, aucun `--cleanup`.
`contents: read` est la seule permission ; aucun secret configuré, environnement,
OIDC, runner self-hosted, service, installation, cache, artefact, déploiement ou
release. Le jeton éphémère de checkout reste limité à la lecture et n'est pas
persisté. `actions/checkout` est épinglée au commit officiel
`3d3c42e5aac5ba805825da76410c181273ba90b1` (v7.0.1), profondeur 1,
sans tags, LFS ni submodules.

Le label fixe est `ubuntu-24.04` x64 standard GitHub-hosted ; le contexte doit
annoncer `github-hosted`. Les préconditions sont vérifiées dans le runner :
Ubuntu 24.04, `/usr/bin/python3` 3.12 sans optimisation, root par `sudo -n`,
systemd PID1 et montage cgroup v2. Aucun hôte connecté existant n'est substituable.
La documentation GitHub décrit ces runners standard comme des VM neuves
par job, avec sudo sans mot de passe ; cela ne remplace pas les gardes runtime.

Références publiques consultées pour cette préparation :
[runners et privilèges](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
[image Ubuntu 24.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md),
[contexte du runner](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#runner-context),
[checkout épinglé](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1).

Avant toute autorisation d'exécution, lire les paramètres distants sans les
modifier et vérifier `enabled=false`, l'absence de la branche dédiée et des
autres runs actifs/en attente ; vérifier de nouveau le SHA local soumis et son
arbre de trois fichiers. L'autorisation doit nommer le SHA du nouveau commit,
sa publication sur cette seule branche, UN run et le changement temporaire des
paramètres Actions. Les paramètres distants n'ont pas été consultés ici.

Après autorisation seulement : prévoir et autoriser le réglage Actions minimal
limitant les actions au checkout officiel, conserver la configuration initiale,
activer temporairement, publier le seul commit approuvé sur la nouvelle branche,
suivre le seul run sans rerun, puis remettre Actions à `enabled=false` et le
vérifier par relecture. Restaurer aussi les autres paramètres éventuellement
modifiés. Ne pas activer des workflows antérieurs ni acheter des crédits.
Une interruption de l'opérateur peut empêcher cette restauration : ce risque
de contrôle doit être traité dans l'autorisation, pas présumé résolu par ce YAML.

## Ressources et collisions

Aucun utilisateur ni groupe n'est créé. Les services tournent sous root
(aucun `User=`) uniquement sur le runner jetable. Namespace exclusif :

- `w9-proof-{general,sync,legacy,web,scheduler}.service` ;
- `w9-proof-legacy.timer` et `w9-proof-trigger.service` ;
- `/var/lib/w9-maintenance-proof` : copie du script, trois sources synthétiques,
  barrières, PID et traces, marqueurs de publication et d'incertitude ;
- sept fichiers d'unités dans `/usr/local/lib/systemd/system` ;
- sept masques `/etc/systemd/system/<unité> -> /dev/null` ;
- cgroups systemd et copies temporaires `LoadCredential` gérées par systemd.

Avant toute mutation, le wrapper vérifie le hash et refuse le répertoire
préexistant, y compris un lien cassé, les noms/drop-ins/liens de dépendance et
alias synthétiques dans les chemins de recherche systemd, les unités chargées
ou installées, jobs, cgroups, répertoires `/run/credentials/<unité>` et processus
synthétiques préexistants.
Un refus de garde ne déclenche aucune fermeture ni nettoyage : la propriété
exclusive du namespace n'est pas acquise. Aucun état préexistant n'est effacé.
Le laboratoire exclut les acteurs privilégiés concurrents et les changements
de namespace entre la garde et l'installation ; ce n'est pas une défense contre
un administrateur hostile.

Les commandes privilégiées du candidat et du wrapper sont limitées aux sept
noms : création des fichiers synthétiques, mask, stop, kill, show et essais
start/restart. `daemon-reload` recharge le manager globalement ; il ne démarre
ni n'arrête volontairement d'unité extérieure. Le wrapper consulte aussi les
inventaires systemd, cgroups et `/proc` sans journaliser de lignes de commande
extérieures. Les commandes utilisent des listes argv, sans `shell=True`, eval,
texte de commande construit ou dépendance à un PATH utilisateur.

## Durée, échecs et confinement

Le pilote W9 fixe `SIGALRM(240)` : cette borne ne couvre pas à elle seule tous
les descendants. Chaque commande W9 est bornée à 10 s ; une fermeture utilise
un budget monotone de 60 s. Les boucles d'attente explicites sont bornées.
Les auxiliaires consumer/pre sont volontairement persistants jusqu'à leur arrêt.

Le wrapper prévoit 45 s de gardes, au plus 255 s d'attente du pilote
(240 + 15 s), jusqu'à 5 s pour sa terminaison et 75 s de confinement.
L'étape est limitée à 7 min, soit 40 s de marge au-delà de ces 380 s ;
checkout est limité à 2 min et le job à 10 min, soit 60 s de marge globale.
Aucun retry. Une course start/mask non observée est un résultat inconclusif,
jamais une raison de relancer silencieusement.

Après acquisition exclusive du namespace, le `finally` du wrapper termine le
groupe de processus créé pour le seul pilote, pose/vérifie/synchronise les sept
masques persistants, recharge systemd, arrête les sept unités et tue uniquement
les processus de leurs services. Il vérifie ensuite inactive/dead, aucun job,
MainPID/ControlPID zéro pour les services, cgroups capturés/courants vides
(`populated 0`) ou disparus sous ces conditions, et aucun processus portant le
chemin synthétique. Une erreur de lecture ou preuve ambiguë refuse le succès.
Le résultat de W9 est évalué séparément : un confinement réussi ne transforme
jamais un échec W9 en réussite.

Sur succès, erreur, timeout ou signal interceptable, il n'existe aucune voie
automatique de réouverture. Aucun masque, fichier vendor, source, marqueur
`uncertain` ou preuve n'est retiré. Avant la première fermeture W9, une mort
non interceptable peut laisser une fermeture incomplète ; un SIGKILL du wrapper,
une annulation forcée ou un timeout dur du job peut empêcher le confinement.
Dans ce cas aucune vérification d'arrêt n'est revendiquée : détruire le runner.
Ne jamais réutiliser cet hôte ni traiter les masques seuls comme une preuve.

Le nettoyage choisi pour cette voie jetable est l'arrêt vérifié quand il est
possible, la fermeture conservée, puis la destruction de la VM par la plateforme
en fin de job. Aucun nettoyage de réutilisation d'un hôte persistant n'est fait.
Le mode `--cleanup` du candidat supprimerait les sept fichiers vendor avant
unmask et conserverait les preuves ; il exige une revue explicite après succès
et n'est ni appelé ni autorisé par cette préparation. Son absence de marqueur
`uncertain` n'est pas, à elle seule, une attestation de succès complet.

Les logs normaux du candidat sont des assertions fixes et les noms des coupures.
Les erreurs peuvent contenir des commandes/chemins et messages systemd du
namespace synthétique. Pas de dump d'environnement, données applicatives ou
credentials réels ; aucune publication d'artefact. Les logs de run seront
visibles selon les règles du dépôt public, ce qui fait partie de l'autorisation.

## Limites de preuve et validation locale

W9 exige l'absence de jobs/PID/cgroup peuplé pour cinq consommateurs ; le wrapper
contrôle aussi timer et trigger en sortie. Le résiduel `KillMode=process` doit
être rejeté avant son SIGKILL ciblé. Les enfants `--mutator` sont tués par SIGKILL
à cinq coupures ; le pilote principal n'est pas lui-même tué par ce scénario.
Les reçus en mémoire et la récupération `old/new` sont des assertions de test,
sans protocole de reprise ou réouverture opérationnel. Le contrôle positif de
réouverture ne retire aucun masque.

Aucun reboot, perte d'alimentation ou perte réelle de `/run` n'est testé.
Des liens sous `/etc` après daemon-reload ne prouvent pas le comportement au
boot. Aucune certification des voies déployées, lancements directs/acteurs
privilégiés hors contrat, mapping réel des trois sources, fencing PostgreSQL,
lifecycle de déploiement ou bascule de services. Aucune suite antérieure ni DB
n'est ajoutée. Une réussite future resterait exclusivement synthétique.

Contrôles statiques locaux : syntaxe du candidat et du wrapper analysée avec
grammaire Python 3.12 et compilation en mémoire ; YAML analysé avec Psych,
clés dupliquées et alias refusés ; triggers, permissions, gardes et appel unique
vérifiés structurellement ; `bash -n` et ShellCheck réussis ; identité exacte,
revue manuelle de tout l'arbre et motifs de secrets vérifiés. Les motifs ne
remplacent pas la revue intégrale. Ruff, Bandit, Gitleaks, actionlint et yamllint
sont indisponibles localement ; rien n'est installé. La vérification Git finale
doit confirmer racine sans parent, seuls trois fichiers, aucun objet/historique
partagé, remote, tag ou alternate, intégrité et diff-check.

Ces contrôles ne lancent ni le candidat, ni le wrapper, ni systemd, ni une
simulation Linux. Leur succès ne valide aucun comportement Linux runtime.

`LINUX_EXECUTION=NOT_RUN` · `WINDOW_CERTIFIED=false` · `GITHUB_PUSH=false` ·
`ACTIONS_SETTINGS_MODIFIED=false` · `PRODUCTION_MODIFIED=false`.
