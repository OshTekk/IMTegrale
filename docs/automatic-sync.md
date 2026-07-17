# Actualisation automatique PASS

## Consentement

L'actualisation planifiée est désactivée pour chaque nouveau compte. Seul le propriétaire connecté avec son compte IMT peut l'activer depuis les paramètres, après une confirmation dédiée. Le choix et sa date sont enregistrés ; un token partagé et l'administrateur ne peuvent pas donner ce consentement à sa place.

La désactivation est immédiate pour tout nouveau démarrage. L'ordonnanceur revérifie le consentement sous le verrou du compte juste avant la connexion à PASS, ce qui ferme aussi la fenêtre entre la sélection d'un compte et son exécution. Une synchronisation réseau déjà commencée peut terminer son traitement.

Les connexions explicitement déclenchées par une connexion IMT, le bouton de synchronisation ou l'administrateur restent indépendantes de cette option.

## Planification

Les fréquences de base proposées sont `2`, `4`, `6`, `8`, `12` ou `24` heures. Deux heures est donc la fréquence maximale. Une exécution automatique ne peut commencer que :

- du lundi au vendredi ;
- entre 8 h incluses et 20 h exclues ;
- selon le fuseau horaire du compte ;
- lorsque l'intervalle choisi est écoulé depuis la dernière tentative, manuelle ou automatique.

L'API exécute un unique ordonnanceur léger toutes les 60 secondes. Ce réveil n'est pas une connexion à PASS : il ne sélectionne qu'un compte consentant, actif, échu et dans sa fenêtre ouvrée. L'ancien `botnote-worker.timer` reste désactivé afin qu'un seul ordonnanceur possède cette responsabilité.

En mode adaptatif, trois exécutions automatiques réussies sans changement font passer à l'intervalle supérieur. Une note ajoutée, modifiée ou archivée rétablit immédiatement la fréquence de base. Lorsqu'un changement est détecté, les autres comptes consentants du même cursus et de la même promotion reviennent aussi à leur base, avec une dispersion déterministe dans leur intervalle : aucune synchronisation de masse immédiate n'est créée.

Les comptes échus sont ordonnés par retard proportionnel. Une demande manuelle est prioritaire, sauf si une actualisation automatique attend depuis au moins un intervalle complet ; ce compte obtient alors le prochain créneau. Tant que plusieurs comptes attendent, le dernier compte automatique servi ne peut pas reprendre immédiatement la place d'un autre compte échu.

Toute réservation automatique actualise aussi le délai de fraîcheur manuel de dix minutes. Elle respecte le verrou PASS global, les quotas glissants, le repos de 60 secondes et le circuit breaker. Le contrat commun est détaillé dans [`manual-sync.md`](manual-sync.md).

## Exploitation

Après une migration ou un déploiement, vérifier que `botnote-worker.timer` est désactivé et que les consentements existants n'ont pas été modifiés. La commande de diagnostic `botnote sync-due` reste disponible, mais ne doit pas être exécutée pendant que l'API de production ordonnance les comptes.

Le portail administrateur affiche l'état et la fréquence choisis pour faciliter le support, sans proposer d'activation administrateur. Les événements `sync:auto_enabled` et `sync:auto_disabled` assurent la traçabilité du choix du propriétaire.
