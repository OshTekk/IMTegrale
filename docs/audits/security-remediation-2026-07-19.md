# Remédiation de sécurité du 19 juillet 2026

## Périmètre et méthode

L'analyse couvre tout le dépôt IMTégrale. Le modèle de menace de référence se trouve dans [`docs/security/threat-model.md`](../security/threat-model.md). Les validations et régressions utilisent uniquement des identités, tokens, cookies, notes, calendriers et réponses HTTP fictifs. Aucun identifiant réel et aucun appel réel à PASS, HUB, INPASS ou Telegram n'ont été utilisés.

Le scan profond a exécuté plusieurs passes indépendantes de découverte, une fusion sémantique, une validation source-vers-sink et une calibration de sévérité. Seuls les chemins reproductibles sont listés comme vulnérabilités. Les recommandations sans chemin exploitable restent classées comme durcissement ou dette.

## Assurance propriétaire primaire

La faiblesse proposée a été **confirmée avant correction** par des tests synthétiques :

1. un token `role=owner` ouvrait une `WebSession` avec `auth_method=token` et un `share_token_id` ;
2. l'ancien garde propriétaire ne distinguait pas cette délégation d'une authentification IMT ou passkey ;
3. la session pouvait demander un challenge d'enrôlement WebAuthn puis créer une passkey, ou créer un autre token `owner` ;
4. la révocation du token initial ne supprimait pas le nouveau facteur ou le nouveau token, qui constituaient donc un accès propriétaire persistant indépendant.

La correction définit une session propriétaire primaire comme `role=owner`, `auth_method in {imt, passkey}` et `share_token_id is None`. `require_primary_owner` et `require_primary_owner_action` appliquent cet invariant côté serveur ; le second conserve les contrôles Origin et CSRF. Un refus retourne de manière stable :

```json
{
  "detail": {
    "code": "PRIMARY_AUTH_REQUIRED",
    "message": "Une authentification IMT ou passkey est requise pour cette opération."
  }
}
```

Les options d'enrôlement, la création et la suppression de passkeys, ainsi que la création d'un token `owner`, exigent désormais cette assurance. Un token `owner` peut encore créer un token `viewer` : cette délégation réduit l'autorité, reste visible, révocable et clôturée par la génération d'accès. Le détail des routes sensibles et leur justification est documenté dans [`docs/security/authentication-assurance.md`](../security/authentication-assurance.md).

## Constats validés et corrigés

Les lignes indiquent la zone vulnérable au moment du scan ; les symboles et tests constituent la référence durable après modification.

| ID | Sévérité | Chemin source-vers-sink validé | Impact et conditions | Correction minimale et preuve |
| --- | --- | --- | --- | --- |
| DSS-C002 | Moyenne | `routers/auth.py:120` vers `auth_protection.py:159-237` | Un client anonyme pouvait alimenter le cooldown global d'un identifiant et bloquer temporairement sa connexion légitime. | Cooldown d'enforcement borné par couple cible/client ; l'état cible seul devient de la télémétrie. Tests de séparation des clients. |
| DSS-C003 | Moyenne | `routers/auth.py:120` vers le circuit d'authentification | Des identifiants invalides distribués pouvaient ouvrir le circuit global et refuser des utilisateurs sans lien. | Le circuit global ne réagit plus qu'aux défaillances amont distribuées, jamais aux erreurs de credentials. Tests dans les deux directions. |
| DSS-C006 | Moyenne | `routers/settings.py:103` vers le consentement auto-sync durable | Un token `owner` délégué pouvait activer une capacité PASS/HUB persistante. | Activation sous `require_primary_owner`; désactivation toujours permise pour contenir un incident. |
| DSS-C007 | Moyenne | `routers/settings.py:145` vers la configuration initiale de sync | Même élévation durable via le parcours d'installation. | Même garde primaire et erreur stable `PRIMARY_AUTH_REQUIRED`. |
| DSS-C008 | Moyenne | `routers/sync.py:36` vers la session PASS/HUB conservée | Une délégation pouvait déclencher du trafic académique avec la capacité du titulaire. | Synchronisation manuelle sous `require_primary_owner_action`, Origin et CSRF inclus. |
| DSS-C011 | Moyenne | `routers/auth.py:353` vers la suppression d'une passkey | Les sessions déjà authentifiées avec le facteur supprimé restaient actives. | Avancement de la génération d'accès et clôture des sessions passkey ; tests de non-régression des facteurs légitimes. |
| DSS-C015 | Moyenne | `routers/admin.py:451` vers l'effacement leaderboard | L'auto-effacement pouvait aussi retirer une suspension administrative et permettre un retour immédiat. | Les données publiques sont effacées, mais la suspension et son motif d'audit sont conservés. |
| DSS-C025 | Moyenne | `routers/admin.py:419` vers une émission concurrente de session | Une session token/passkey validée tardivement pouvait survivre à `revoke_access`. | Génération d'accès sur comptes, facteurs, tokens et sessions, incrémentée sous verrou ; migration `0019` et test de concurrence. |
| DSS-C026 | Moyenne | `routers/calendars.py:67` vers le fetch INPASS | Deux requêtes concurrentes pouvaient franchir le quota avant son écriture. | Réservation avant I/O sous verrou local et verrou consultatif PostgreSQL, puis finalisation idempotente. Test concurrent synthétique. |
| DSS-C029 | Moyenne | `routers/auth.py:183` vers l'acceptation SSO | Une page terminale 2xx inconnue sur une origine autorisée pouvait être assimilée à une authentification primaire réussie. | Acceptation uniquement de pages protégées reconnues positivement ; pages inconnues, login et sosies échouent fermées. |
| DSS-C042 | Faible | `routers/leaderboard.py:40` vers le calcul du classement | Une lecture pouvait matérialiser et recalculer une population inter-comptes non bornée. | Filtrage du segment en SQL et plafond stable de 1 000 participants avec `LEADERBOARD_CAPACITY_EXCEEDED`. |

Les contre-mesures préexistantes réduisaient certaines fenêtres (quotas, expirations, CSRF, Origin, révocation, allowlists), mais ne coupaient aucun des chemins ci-dessus. C'est la raison pour laquelle ils ont été conservés comme constats validés.

## Durcissements connexes

- `secure_compare` retourne `False` sur une entrée non ASCII au lieu de provoquer une erreur 500 ;
- les clients IMT et Telegram emploient une `requests.Session` dédiée avec `trust_env=False` ;
- une authentification CAS valide sans cookie PASS réutilisable ne provoque plus de 500 : la connexion réussit et la synchronisation automatique reste en pause ;
- les snapshots PASS/HUB restent chiffrés, bornés, filtrés par hôte et restaurés avec l'attribut `Secure` ;
- les décisions frontend (`isPrimaryOwner`) ne servent qu'à l'ergonomie : chaque autorisation reste contrôlée par FastAPI.

## Vérification et déploiement

- Ruff backend et scripts : vert ;
- 696 tests backend : verts ; couverture 87,45 % lignes et 69,20 % branches ;
- 12 contrats PostgreSQL et migrations `base -> 0024 -> base -> 0024` : verts ;
- 74 tests Vitest et 25 parcours Playwright/axe : verts ;
- typecheck, lint, format, build et budgets frontend : verts ;
- `pip-audit` et `pnpm audit --prod` : aucune vulnérabilité connue ;
- scan de 359 fichiers : aucun secret détecté ;
- wheel, frontend, SBOM, manifeste et smoke test de release : verts ;
- release `20260719T190514Z` active, schéma Alembic `0024`, API, scheduler et trois workers actifs ;
- contrôle d'exploitation sans alerte, sauvegarde chiffrée et timer actifs, readiness HTTPS `200` en version `4.5.6` ;
- portail d'administration absent de l'entrée Internet publique.

## Risques résiduels et suite séparée

Les contrats PASS/HUB/INPASS restent non officiels et peuvent évoluer. Telegram conserve une sémantique distante au moins une fois : une duplication rare reste possible après acceptation distante et crash avant acquittement local. Le plafond du leaderboard est un échec borné, pas encore une pagination.

Une session passkey prouve la possession au moment de la connexion, pas une preuve récente pour chaque mutation sensible. Un véritable step-up étudiant doit faire l'objet d'une PR distincte : enregistrer `primary_verified_at`, demander une nouvelle preuve IMT ou WebAuthn de courte durée, et lier un challenge à la session, au compte, à la famille d'action, à l'Origin et à une expiration. Cette évolution n'est pas nécessaire pour fermer la chaîne validée et n'a donc pas été introduite dans cette intervention.
