# Actualisation automatique PASS

## Consentement

L'actualisation planifiée est désactivée pour chaque nouveau compte. Seul le propriétaire connecté avec son compte IMT peut l'activer depuis les paramètres, après une confirmation dédiée. Le choix et sa date sont enregistrés ; un token partagé et l'administrateur ne peuvent pas donner ce consentement à sa place.

La désactivation est immédiate pour tout nouveau démarrage. L'ordonnanceur revérifie le consentement sous le verrou du compte juste avant la connexion à PASS, ce qui ferme aussi la fenêtre entre la sélection d'un compte et son exécution. Une synchronisation réseau déjà commencée peut terminer son traitement.

Les connexions explicitement déclenchées par une connexion IMT, le bouton de synchronisation ou l'administrateur restent indépendantes de cette option.

## Session technique bêta

Le mot de passe IMT n'est jamais conservé. Après une authentification réussie, seuls les cookies appartenant exactement à PASS et au Hub COMPETENCES sont retenus. PASS pouvant encore émettre un cookie historique sans attribut `Secure`, IMTégrale ne le réutilise que vers les origines HTTPS autorisées et le normalise systématiquement avec `Secure` avant sa persistance. Leur instantané est chiffré en AES-256-GCM avec un contexte lié à la session, ne contient aucun cookie CAS ou tiers et expire localement au plus tard après 30 jours. Si PASS ne fournit aucune session réutilisable, la connexion reste valide mais l'actualisation automatique demeure en attente d'une reconnexion au lieu de provoquer une erreur serveur.

Cette durée est une borne d'observation, pas une promesse de PASS. Le service distant peut fermer la session plus tôt. Dans ce cas, IMTégrale détruit immédiatement l'instantané chiffré, marque l'automatisation `reauth_required` et n'effectue aucun nouvel appel planifié avant une reconnexion explicite. La reconnexion renouvelle la session mais ne synchronise pas les notes.

Le premier parcours propose le mode manuel par défaut ou l'automatique facultatif. Le portail administrateur expose l'état par compte et uniquement des métriques de longévité agrégées sur 24 heures, 3 jours, 7 jours et 30 jours ; aucune valeur de cookie n'est renvoyée.

## Planification

Les fréquences de base proposées sont `2`, `4`, `6`, `8`, `12` ou `24` heures. Deux heures est donc la fréquence maximale. Une exécution automatique ne peut commencer que :

- du lundi au vendredi ;
- entre 8 h incluses et 20 h exclues ;
- selon le fuseau horaire du compte ;
- lorsque l'intervalle choisi est écoulé depuis la dernière tentative, manuelle ou automatique.

Le service systemd `botnote-scheduler.service`, séparé de l'API, examine les échéances toutes les 60 secondes. Ce réveil n'est pas une connexion à PASS : il sélectionne au plus un compte consentant, actif, échu et dans sa fenêtre ouvrée, puis écrit une demande et un job PostgreSQL dans la même transaction. `botnote-job-worker@sync.service` réclame ensuite le travail avec `FOR UPDATE SKIP LOCKED`. L'ancien `botnote-worker.timer` reste désactivé afin qu'un seul ordonnanceur possède cette responsabilité.

Une demande HTTP acceptée répond immédiatement `202` et reste consultable dans l'état de synchronisation. Elle ne dépend plus de la durée de vie du processus API. Chaque job possède une clé d'idempotence, un bail de 15 minutes, trois tentatives bornées avec backoff et un état `dead_letter`. Un worker interrompu avant acquittement laisse donc le job récupérable après expiration du bail. Les mutations académiques et la création d'une notification Telegram sont commitées ensemble ; le worker `outbox` ne déchiffre le token et le chat Telegram qu'au moment de l'envoi.

En mode adaptatif, trois exécutions automatiques réussies sans changement font passer à l'intervalle supérieur. Une note ajoutée, modifiée ou archivée rétablit immédiatement la fréquence de base. Lorsqu'un changement est détecté, les autres comptes consentants du même cursus et de la même promotion reviennent aussi à leur base, avec une dispersion déterministe dans leur intervalle : aucune synchronisation de masse immédiate n'est créée.

Les comptes échus sont ordonnés par retard proportionnel. Une demande manuelle est prioritaire, sauf si une actualisation automatique attend depuis au moins un intervalle complet ; ce compte obtient alors le prochain créneau. Tant que plusieurs comptes attendent, le dernier compte automatique servi ne peut pas reprendre immédiatement la place d'un autre compte échu.

Toute réservation automatique actualise aussi le délai de fraîcheur manuel de dix minutes. Elle respecte le verrou PASS global, les quotas glissants, le repos de 60 secondes et le circuit breaker. Le contrat commun est détaillé dans [`manual-sync.md`](manual-sync.md).

## Exploitation

Après la migration `0017`, tous les anciens mots de passe chiffrés sont supprimés de façon irréversible. Les consentements automatiques restent enregistrés, mais les comptes concernés sont mis en pause jusqu'à leur prochaine authentification IMT. Depuis la migration additive `0020`, vérifier que `botnote-scheduler.service` et les instances `botnote-job-worker@sync.service`, `botnote-job-worker@calendar.service` et `botnote-job-worker@outbox.service` sont actives, et que `botnote-worker.timer` demeure désactivé. La commande de diagnostic `botnote sync-due` ne contacte plus PASS : elle réserve le prochain compte échu dans la file durable.

Les jobs réussis sont conservés 7 jours, les notifications livrées 30 jours et les états `dead_letter` 90 jours. La maintenance horaire applique ces rétentions ainsi que celles des métriques PASS et calendrier. Une notification Telegram est livrée au moins une fois : un crash après acceptation par Telegram mais avant l'acquittement PostgreSQL peut produire un doublon, car Telegram ne fournit pas de clé d'idempotence exploitable.

Le portail administrateur affiche l'état et la fréquence choisis pour faciliter le support, sans proposer d'activation administrateur. Les événements `sync:auto_enabled` et `sync:auto_disabled` assurent la traçabilité du choix du propriétaire.
