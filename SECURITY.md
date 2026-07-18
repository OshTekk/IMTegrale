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
