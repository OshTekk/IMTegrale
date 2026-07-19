# Politique de sécurité

## Signaler une vulnérabilité

Utilisez exclusivement le formulaire privé **Security > Advisories > Report a vulnerability** du dépôt GitHub. Ne publiez pas de vulnérabilité exploitable dans une issue, une discussion ou une pull request.

Le signalement doit préciser la version ou le commit concerné, le scénario d'attaque, l'impact observé et, si possible, une reproduction minimale sans donnée réelle. N'incluez jamais d'identifiant IMT, de mot de passe, de token Telegram, de cookie de session ni d'export PASS.

Un accusé de réception est visé sous sept jours. La correction, la publication coordonnée et l'attribution sont ensuite convenues selon la gravité et la complexité du problème.

## Périmètre supporté

Seule la branche `main` à jour est supportée. Les forks, déploiements modifiés, erreurs de configuration réseau et versions historiques ne sont pas couverts.

## Limites de confiance

IMTégrale reçoit le mot de passe IMT pendant une authentification CAS, en mémoire et via HTTPS, mais ne l'écrit ni dans PostgreSQL ni dans les journaux. Après l'authentification, seuls les cookies `Secure` appartenant exactement aux domaines PASS et Hub autorisés peuvent être retenus. Ils sont filtrés, chiffrés en AES-256-GCM, révocables et supprimés au plus tard après 30 jours.

Cette session technique reste une capacité d'accès : le serveur doit pouvoir la déchiffrer pour synchroniser. Une compromission simultanée de l'application et de sa clé maître pourrait donc permettre son utilisation jusqu'à son expiration ou sa révocation. PASS ne fournit pas ici de délégation OAuth, et le code source public ne prouve pas à lui seul quelle version est exécutée par une instance donnée.

Une instance auto-hébergée peut réserver à son unique compte propriétaire un mot de passe local hors base, hors dépôt et lisible uniquement par l'utilisateur système du service. Cette exception est désactivée par défaut, ne doit jamais être proposée à un compte public et augmente explicitement le risque accepté par cet exploitant.

Avant toute exposition Internet, l'administrateur doit adapter les exemples de `deploy/`, isoler les secrets hors Git, tester une restauration de sauvegarde chiffrée et limiter l'administration à une identité réseau privée.

Le [modèle de menace](docs/security/threat-model.md), la [procédure de rotation](docs/security/key-rotation.md) et les [niveaux d'assurance](docs/security/authentication-assurance.md) font partie de la politique maintenue.

## Niveaux d'assurance propriétaire

Une session `owner` n'est pas nécessairement une authentification primaire. Une connexion par token reste une délégation liée au token qui l'a créée, même si ce token possède le rôle `owner`.

Une **session propriétaire primaire** respecte simultanément les invariants suivants :

- `role == "owner"` ;
- `auth_method` vaut `imt` ou `passkey` ;
- aucun `share_token_id` n'est associé à la session.

Les actions mutatives conservent en plus les contrôles Origin et CSRF. Une session déléguée qui tente une opération primaire reçoit un `403` stable :

```json
{
  "detail": {
    "code": "PRIMARY_AUTH_REQUIRED",
    "message": "Une authentification IMT ou passkey est requise pour cette opération."
  }
}
```

Le frontend masque les commandes incompatibles et explique la reconnexion nécessaire, mais ce masquage est uniquement ergonomique. Les dépendances FastAPI appliquent l'autorisation indépendamment du client.

## Inventaire des opérations sensibles

| Opération | Routes | Assurance exigée | Raison |
| --- | --- | --- | --- |
| Lister, ajouter ou supprimer une passkey | `GET /api/v1/auth/passkeys`, `POST /api/v1/auth/passkeys/registration/options`, `POST /api/v1/auth/passkeys`, `DELETE /api/v1/auth/passkeys/{id}` | Propriétaire primaire | Une passkey est un facteur propriétaire persistant et indépendant du token courant |
| Terminer l'installation de sécurité | `POST /api/v1/auth/security-setup/complete` | Propriétaire primaire | Une délégation ne doit pas pouvoir supprimer le rappel d'installation du titulaire |
| Créer un token `owner` | `POST /api/v1/tokens` avec `role=owner` | Propriétaire primaire | Le token donne un accès propriétaire persistant et révocable |
| Créer un token `viewer` | `POST /api/v1/tokens` avec `role=viewer` | Toute session `owner`, avec Origin et CSRF | Délégation volontaire de lecture seule, sans hausse de privilège ; ce choix permet à un token `owner` de partager une vue limitée |
| Lister ou révoquer un token | `GET /api/v1/tokens`, `DELETE /api/v1/tokens/{id}` | Toute session `owner` ; CSRF pour la révocation | La révocation réduit l'accès et supprime immédiatement toutes les sessions issues du token |
| Remplacer le token ou le Chat ID Telegram | `PUT /api/v1/settings/telegram` | Propriétaire primaire | Le secret remplace durablement la destination des notifications futures |
| Activer, suspendre ou tester une configuration Telegram existante | `PATCH /api/v1/settings/telegram`, `POST /api/v1/settings/telegram/test` | Toute session `owner`, avec Origin et CSRF | Ces opérations ne révèlent ni ne remplacent le secret ; le test envoie uniquement le message technique prévu |
| Enregistrer ou supprimer le secret INPASS | `PUT /api/v1/calendar/subscription`, `DELETE /api/v1/calendar/subscription` | Propriétaire primaire | L'URL iCalendar est un secret personnel chiffré |
| Consulter agenda, simulations et relevé PDF personnels | `/api/v1/calendar/*`, `/api/v1/simulations/*`, `/api/v1/note-simulations/*`, `GET /api/v1/academic-reports/personal.pdf` | Propriétaire primaire | Ces surfaces sont explicitement privées et le PDF est un export académique |
| Renouveler la session PASS/HUB | `POST /api/v1/auth/pass/reconnect` | Session `owner`, Origin/CSRF et vérification du mot de passe IMT dans l'opération | Le mot de passe frais constitue la preuve primaire propre à cette opération et n'est pas conservé |
| Lancer une synchronisation PASS/HUB | `POST /api/v1/sync` | Propriétaire primaire | L'opération utilise une capacité PASS/HUB conservée pour le titulaire ; une délégation ne peut pas la déclencher |
| Autoriser l'actualisation automatique | `PATCH /api/v1/settings/auto-sync` ou `PUT /api/v1/settings/sync-setup` avec `enabled=true` | Propriétaire primaire | Le consentement rend durable l'utilisation planifiée de la session PASS/HUB. Une session `owner` déléguée peut toujours désactiver l'automatisation immédiatement |
| Publier ou retirer les données de classement | `POST` ou `DELETE /api/v1/leaderboard/*` | Toute session `owner`, avec Origin et CSRF | Le retrait et l'effacement doivent rester immédiats. La publication est candidate à un step-up récent, sans changement de comportement dans ce correctif |
| Supprimer une simulation | `DELETE /api/v1/simulations/{id}`, `DELETE /api/v1/note-simulations/{id}` | Propriétaire primaire | Données privées modifiables uniquement par le titulaire |
| Remplacer le mot de passe administrateur | `POST /api/v1/admin/auth/password` | Réseau privé, mot de passe actuel, CSRF, MFA et step-up passkey récent après l'initialisation | Toutes les anciennes sessions admin sont révoquées |
| Ajouter ou supprimer une passkey admin | `/api/v1/admin/auth/passkeys*` | Mot de passe récent pour la première ; MFA et step-up récent pour les suivantes | La dernière passkey ne peut pas être supprimée |
| Muter un compte, ses accès ou ses autorisations en administration | Routes mutatives `/api/v1/admin/accounts/*` et `/api/v1/admin/pass/probe` | Session admin réseau privé, CSRF, passkey et step-up inférieur à dix minutes | Opérations auditées ; motifs et confirmations restent appliqués |

La création `viewer` par un token `owner` est intentionnelle mais laisse un risque résiduel : si le token parent est compromis, l'attaquant peut créer un accès de lecture seule qui ne disparaît pas avec la révocation du parent. Cet accès reste visible et révocable dans la liste des tokens ; une relation parent-enfant et une révocation en cascade pourront être étudiées séparément si le modèle de délégation évolue.

## Révocation et protection des connexions

Les comptes, sessions, tokens et passkeys portent une génération d'accès. L'action administrative `revoke_access` incrémente cette génération avant de supprimer les accès visibles ; une session ou un token créé tardivement par une requête déjà en vol reste lié à l'ancienne génération et est refusé. La suppression d'une passkey ferme toutes les sessions passkey et fait progresser les autres accès légitimes vers la nouvelle génération afin de fermer la même fenêtre de concurrence sans révoquer inutilement les tokens restants.

Les échecs liés à un identifiant IMT choisi par le client sont conservés pour la télémétrie et l'assistance, mais ne bloquent jamais cet identifiant avant une vérification valide des credentials. Les limites par client restent appliquées. Le circuit global d'authentification est alimenté uniquement par des défaillances amont distribuées, pas par des mots de passe invalides envoyés par des clients.

## Step-up récent étudiant proposé

Le garde primaire atteste la provenance de la session, pas la fraîcheur de l'authentification. Une passkey ou une session IMT vieille de plusieurs semaines satisfait donc encore ce garde.

Un véritable step-up étudiant doit faire l'objet d'une modification séparée : enregistrer côté serveur un `primary_verified_at` lié à la session, demander une nouvelle assertion WebAuthn ou une authentification IMT pour les opérations ciblées, puis accepter cette preuve pendant une fenêtre courte, par exemple dix minutes. Le challenge devrait être lié à la session et à la famille d'action afin d'éviter sa réutilisation pour une opération plus sensible.

Les premières candidates sont la création/suppression de facteurs, la création d'un token `owner`, le remplacement des secrets Telegram/INPASS, la publication au classement et l'export académique. Le portail administrateur possède désormais son propre step-up WebAuthn ; il ne doit pas être confondu avec cette proposition pour les comptes étudiants.
