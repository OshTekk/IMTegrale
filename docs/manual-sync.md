# Synchronisation manuelle PASS

## Politique

Une connexion IMT ou une synchronisation acceptée ouvre un délai de `600` secondes pour le compte. Le délai est calculé exclusivement avec l'horloge UTC du serveur : changer l'heure du navigateur, ouvrir une autre session ou utiliser un autre appareil ne permet pas de le contourner.

Une tentative acceptée consomme le délai dès sa réservation, qu'elle réussisse, échoue ou expire. Une synchronisation automatique compte également comme donnée fraîche. Le propriétaire doit donc attendre le reliquat avant une nouvelle demande manuelle. L'administrateur peut forcer une synchronisation pour le support ; le contournement de quota exige un motif, ne contourne jamais le verrou global ni le repos inter-requêtes, et produit l'événement d'audit `account.sync_forced`.

Il n'existe aucune file réseau parallèle : une seule opération PASS peut être active pour toute l'instance et deux opérations sont séparées d'au moins 60 secondes. Chaque compte dispose d'un budget glissant de 3 opérations par heure et 8 par 24 heures. Une opération réellement commencée compte même en cas d'échec ou de timeout ; un refus avant réseau ne consomme aucun budget.

Une réponse `429` ouvre immédiatement le circuit global pendant le `Retry-After` fourni par PASS. Des `403` sur plusieurs comptes distincts ou des erreurs réseau/amont répétées l'ouvrent également. À l'échéance, une seule sonde est admise en état semi-ouvert. Les données déjà importées restent disponibles pendant toute interruption.

## Contrat HTTP

`GET /api/v1/sync/status` et `POST /api/v1/sync` sont réservés au propriétaire IMT. L'état est également présent dans `account.manual_sync` du dashboard propriétaire ; les sessions par token reçoivent `null` afin de ne pas exposer les identifiants, acteurs ou horaires internes.

Le client envoie un en-tête `Idempotency-Key` stable pour une même intention, entre 8 et 128 caractères sûrs. Le serveur n'en conserve que le SHA-256 lié au compte. Une répétition renvoie la demande d'origine sans relancer PASS : `202` tant qu'elle est active, puis `200` lorsqu'elle est terminée.

Les refus sont structurés :

- `429 SYNC_COOLDOWN` avec `Retry-After`, `retry_after_seconds`, `available_at` et `server_time` lorsque le délai court encore ;
- `409 SYNC_IN_PROGRESS` avec le reliquat du bail lorsqu'une demande est déjà active ;
- `422 SYNC_INVALID_IDEMPOTENCY_KEY` lorsque la clé est invalide.
- `429 PASS_ACCOUNT_QUOTA` lorsque le budget glissant du compte est atteint ;
- `429 PASS_QUIET_PERIOD` pendant le repos global ;
- `503 PASS_CIRCUIT_OPEN` lorsque la protection amont est active.

Le compte à rebours de l'interface est seulement une projection accessible de la réponse serveur. À son terme, l'interface redemande l'état ; elle ne réactive jamais le bouton localement sans cette confirmation.

## Exécution et reprise

La réservation atomique en base précède le lancement du worker. Un verrou fichier par compte reste une seconde barrière pendant l'accès à PASS. La demande possède un bail de `900` secondes : une réservation active dont le bail a expiré est marquée `failed` avec le code `SYNC_WORKER_LOST`, puis le compte peut être réservé à nouveau.

Les résultats finaux sont `succeeded`, `failed` ou `skipped`. Une réponse PASS partielle ou invalide est rejetée avant toute écriture de notes. Les erreurs persistées et journalisées utilisent uniquement des codes sûrs ; ni identifiant IMT, ni secret, ni contenu de note ne figure dans les logs de contrôle. Les identités de compte et de connexion utilisées par les protections sont des références HMAC non réversibles.

Les cent dernières demandes terminales par compte sont conservées. Les métriques minimales d'exploitation se déduisent des statuts, acteurs, dates d'acceptation et d'achèvement, ainsi que des événements `sync:accepted`, `sync:rejected_cooldown`, `sync:completed` et `sync:error`.

## Matrice de fraîcheur

| Origine | Délai actualisé | Délai contourné | Exclusion mutuelle |
| --- | --- | --- | --- |
| Connexion IMT réussie | Oui | Sans objet | Sans objet |
| Bouton propriétaire | Oui, à l'acceptation | Non | Oui |
| Worker automatique | Oui, à l'acceptation | Oui pour sa réservation | Oui |
| Portail administrateur | Oui, à l'acceptation | Oui, audité | Oui |
