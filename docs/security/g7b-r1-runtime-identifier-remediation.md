# G7B R1 — isolation des identifiants runtime

## Classification agrégée de l'incident worker

L'historique PostgreSQL reste intact. Les six `SYNC_WORKER_LOST` du compte
canary actif sont tous des requêtes automatiques créées et acceptées avant
l'activation autonome :

| Propriété | Classification agrégée |
| --- | --- |
| Création corrélée `sync:accepted` | 6 avant activation, 0 après |
| Acceptation | 6 avant activation, 0 après |
| Début d'exécution | 0 démarrée |
| Lease | 5 échus avant activation, 1 borné après |
| Fin | 5 avant activation, 1 après |
| Frontière stricte | 1 acceptée avant puis terminée après ; 0 acceptée après puis perdue |
| Retry | chaîne historique de nouvelles requêtes ; aucun job durable encore retenu |
| Succès ultérieur | 6 sur 6 |

Le cas frontière a été accepté 180 secondes avant l'activation, terminé 542
secondes après, avec une borne de lease à 721 secondes après l'activation. Le
sync worker est resté dans la même invocation pendant cette fenêtre. Les
`operations-check` étaient verts jusqu'à 431 secondes après l'activation, puis
ont signalé un heartbeat stale à partir de 742 secondes. Aucun redémarrage du
worker et aucune nouvelle requête `SYNC_WORKER_LOST` acceptée après activation
n'ont été observés. Cette classification est historique et n'est pas réécrite
par R1.

## Matrice des consommateurs effective

| Valeur logique | Web | Scheduler | Sync worker | Operations | Calendar | Outbox |
| --- | --- | --- | --- | --- | --- | --- |
| `autonomous_sync_canary_account_ids` | disponibilité et métriques privées | sélection et pause du planning | autorisation d'exécution | — | — | — |
| `owner_imt_username` | — | — | fallback propriétaire existant | — | — | — |
| `learning_allowed_imt_usernames` | contrôle d'accès Parcours personnel | — | — | — | — | — |

## Invariant R1

En production, ces trois valeurs sont refusées dans `Environment`, y compris
sous leur ancienne forme vide. Elles ne peuvent être chargées que depuis un
credential systemd mappé au rôle consommateur. Les sources du credstore restent
`root:root 0400`; les unités ne peuvent pas lire directement le credstore.
`operations-check` charge un profil distinct, sans secret et sans identifiant.
