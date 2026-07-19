# Assurance d'authentification

## Étudiants et délégations

Une session propriétaire primaire possède `role=owner`, utilise `auth_method=imt` ou `passkey` et n'est liée à aucun token partagé. Les facteurs persistants, secrets personnels, exports privés, synchronisations et tokens `owner` exigent ce niveau côté FastAPI. Le frontend ne fait qu'en refléter l'état.

Une session issue d'un token `owner` peut créer un token `viewer` afin de déléguer volontairement la lecture. Elle ne peut créer ni passkey ni autre token `owner`. La révocation du token parent ferme ses sessions ; les tokens `viewer` créés restent des délégations séparées, visibles et révocables.

Un step-up récent étudiant n'est pas encore généralisé. Les opérations candidates sont la gestion des facteurs, le remplacement d'un secret, la publication, l'export et l'effacement. Son ajout devra lier une nouvelle preuve IMT ou WebAuthn à la session et à la famille d'action, sans assimiler l'ancienneté d'une session primaire à une preuve récente.

### Inventaire des accès persistants et données sensibles

| Route ou famille | Assurance exigée | Justification |
| --- | --- | --- |
| `POST /api/v1/auth/passkeys/registration/options`, `POST /api/v1/auth/passkeys`, `DELETE /api/v1/auth/passkeys/{id}` | Propriétaire primaire + Origin + CSRF | Ajoute ou retire un facteur capable de survivre à la session courante. La suppression invalide aussi les sessions passkey par génération d'accès. |
| `POST /api/v1/tokens` avec `role=owner` | Propriétaire primaire + Origin + CSRF | Rend un accès propriétaire persistant. Un token `owner` ne peut donc pas se reproduire. |
| `POST /api/v1/tokens` avec `role=viewer` | Propriétaire, Origin + CSRF | Délégation atténuée intentionnelle. Un token `owner` peut créer ce lecteur, qui reste indépendant, visible, révocable et borné par la génération d'accès. |
| `DELETE /api/v1/tokens/{id}` | Propriétaire, Origin + CSRF | La révocation réduit l'autorité ; elle reste possible depuis une délégation propriétaire afin de contenir un incident. |
| `PUT /api/v1/settings/telegram` | Propriétaire primaire + Origin + CSRF | Remplace deux secrets chiffrés. Le simple arrêt des notifications et le test restent des actions propriétaire sans remplacement de secret. |
| `PUT` et `DELETE /api/v1/calendars/subscription` | Propriétaire primaire + Origin + CSRF | Ajoute ou supprime une URL calendrier secrète et une capacité de récupération persistante. Les lectures du calendrier sont également réservées au titulaire primaire. |
| `POST /api/v1/auth/pass/reconnect` et `POST /api/v1/sync` | Propriétaire primaire + Origin + CSRF | Manipule une authentification IMT temporaire ou réutilise une session PASS/HUB conservée ; aucune délégation ne peut déclencher ce trafic. |
| `PATCH /api/v1/settings/auto-sync`, `PUT /api/v1/settings/sync-setup` | Propriétaire primaire pour activer, propriétaire pour désactiver | L'activation rend l'accès PASS/HUB durable côté scheduler. La désactivation réduit l'autorité et reste toujours disponible. |
| `GET /api/v1/academic-reports/personal.pdf` | Propriétaire primaire | Exporte identité et données académiques. Le téléchargement est borné, non mis en cache et interdit aux tokens. |
| `DELETE /api/v1/leaderboard/participation`, `DELETE /api/v1/leaderboard/data` | Propriétaire + Origin + CSRF | Ce sont des retraits de publication et des droits de confidentialité ; imposer une réauthentification primaire empêcherait la sortie immédiate souhaitée. |
| Suppression complète d'un compte et révocation globale | Administrateur privé + MFA + step-up récent | Il n'existe pas de route publique de suppression complète. L'action administrative est auditée et clôt tous les accès. |

Le masquage de ces commandes dans React n'est qu'une aide ergonomique. Les dépendances FastAPI ci-dessus constituent le contrôle d'autorisation.

## Administration

Le portail admin cumule :

1. une identité `lan:` ou `tailnet:` exacte, produite par le frontal de confiance ;
2. un mot de passe administrateur scrypt ;
3. une passkey WebAuthn avec vérification utilisateur.

Après le mot de passe initial, l'interface impose l'enrôlement de la première passkey avant toute donnée administrative. À chaque nouvelle session, le mot de passe ouvre seulement une session partielle ; une assertion passkey est nécessaire pour consulter le portail. Les anciens cookies créés avant la migration MFA n'ont pas de preuve de mot de passe datée et sont refusés.

Les challenges sont valables cinq minutes, supprimés atomiquement et liés à l'utilisateur et à la session admin. La dernière passkey ne peut pas être supprimée depuis l'API.

## Inventaire du step-up admin

La passkey vérifiée reste le second facteur de la session. Les mutations suivantes exigent en plus une assertion datant de moins de dix minutes :

- ajout ou suppression d'une passkey admin et changement ultérieur du mot de passe ;
- création ou révocation d'une autorisation Parcours ;
- activation, désactivation, révocation d'accès ou correction d'un compte ;
- synchronisation forcée et sonde PASS ;
- correction ou modération du leaderboard ;
- suppression d'un token étudiant ;
- suppression définitive d'un compte.

Les lectures de comptes, métriques, sessions techniques et journal d'audit exigent le MFA de session, mais pas un step-up de dix minutes. La déconnexion reste possible sans MFA afin de permettre une sortie sûre d'une session partielle.
