# Politique de sécurité

## Signaler une vulnérabilité

Utilisez exclusivement le formulaire privé **Security > Advisories > Report a vulnerability** du dépôt GitHub. Ne publiez pas de vulnérabilité exploitable dans une issue, une discussion ou une pull request.

Le signalement doit préciser la version ou le commit concerné, le scénario d'attaque, l'impact observé et, si possible, une reproduction minimale sans donnée réelle. N'incluez jamais d'identifiant IMT, de mot de passe, de token Telegram, de cookie de session ni d'export PASS.

Un accusé de réception est visé sous sept jours. La correction, la publication coordonnée et l'attribution sont ensuite convenues selon la gravité et la complexité du problème.

## Périmètre supporté

Seule la branche `main` à jour est supportée. Les forks, déploiements modifiés, erreurs de configuration réseau et versions historiques ne sont pas couverts.

## Limites de confiance

IMTégrale chiffre les identifiants nécessaires aux synchronisations automatiques, mais le serveur doit pouvoir les déchiffrer. Une compromission simultanée de l'application et de sa clé maître compromet donc ces secrets. PASS ne fournit pas ici de délégation OAuth ; cette limite est documentée dans le produit et dans le README.

Avant toute exposition Internet, l'administrateur doit adapter les exemples de `deploy/`, isoler les secrets hors Git, tester une restauration de sauvegarde chiffrée et limiter l'administration à une identité réseau privée.
