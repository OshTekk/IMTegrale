# Exploitation et observabilité

## Journaux

Les processus `botnote` écrivent une ligne JSON par événement. Le formatter conserve uniquement l'horodatage, le niveau, le logger, le message expurgé, le `correlation_id` et une liste fermée de champs techniques. Il exclut volontairement l'identité du compte, les URL, paramètres, notes, cookies, mots de passe et tokens. Les motifs de secrets connus sont également remplacés avant émission.

Nginx reste propriétaire du journal d'accès réseau expurgé. Uvicorn est lancé sans access log et sans reconfiguration de logging afin que le format JSON reste uniforme.

## Corrélation

Chaque requête HTTP reçoit un UUID `X-Correlation-ID`. Une valeur cliente n'est reprise que si elle est un UUID valide. L'identifiant est persisté avec la demande de synchronisation, le job durable et l'outbox, puis restauré dans le contexte du worker. Il permet donc de suivre une action sans journaliser l'identifiant du compte ni le contenu traité.

Les tâches automatiques et chaque cycle de scheduler reçoivent un nouvel UUID. Les anciennes lignes créées avant la migration `0022` peuvent garder une corrélation nulle.

## Métriques privées

`GET /api/v1/admin/operations/metrics` est protégé par le réseau administratif privé, la session admin, le mot de passe changé et la passkey MFA. Il expose uniquement des agrégats :

- nombre, erreur, moyenne et p95 des requêtes du processus API courant ;
- connexions SSE ouvertes et cumulées depuis son démarrage ;
- profondeur, ancienneté et dead-letters des files PostgreSQL ;
- état et âge des heartbeats scheduler, sync, calendrier et outbox ;
- état du circuit et quotas PASS, sans cible ni opération individuelle ;
- tentatives et erreurs iCalendar sur 24 heures.

Les compteurs HTTP/SSE en mémoire repartent à zéro au redémarrage. Les files, heartbeats, opérations PASS et tentatives calendrier sont durables. Aucun label à cardinalité utilisateur n'est accepté.

## Santé et alertes

`/health/live` vérifie uniquement que le processus répond. En production, `/health/ready` vérifie PostgreSQL, la tête Alembic attendue et un heartbeat frais pour chacun des quatre processus internes. Il ne contacte jamais PASS, HUB, INPASS ou Telegram et ne renvoie pas le détail d'une panne au public.

`botnote operations-check` produit un objet JSON contenant seulement des codes stables et échoue si une migration ou un heartbeat est périmé, si une file dépasse quinze minutes, si une dead-letter ou un lease expiré existe, ou si le circuit PASS n'est pas fermé. `botnote-operations-check.timer` l'exécute toutes les cinq minutes ; systemd et la supervision de l'hôte relaient l'échec sans donnée personnelle.

## Restauration

Le dump quotidien est compressé puis envoyé directement à `age`, sans fichier clair. Une copie chiffrée est transférée chaque mois vers un hôte de validation distinct qui détient la clé privée. `restore-test.sh` refuse tout nom autre que `botnote_restore_test`, déchiffre directement vers `pg_restore`, vérifie la tête Alembic et écrit uniquement la date du dernier succès. La procédure complète et les permissions sont dans [`deploy/README.md`](../deploy/README.md).

## Polling SSE

Le flux SSE conserve actuellement son polling PostgreSQL borné à deux secondes et cent événements. Aucun résultat de charge ne justifie encore la complexité opérationnelle de `LISTEN/NOTIFY` ni une connexion PostgreSQL dédiée par flux. Les métriques `sse.active`, latence API et activité PostgreSQL doivent être observées avant cette évolution. La décision doit être revue si les connexions simultanées ou la charge de polling deviennent mesurables ; elle ne doit pas être prise sur une projection de trafic.
