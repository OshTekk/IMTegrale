# Données et choix utilisateur

Ce document décrit le comportement du logiciel IMTégrale `4.7.0`. Il aide un étudiant à comprendre les données utilisées par le service et les choix qui restent sous son contrôle.

## Principes

- IMTégrale est un projet étudiant indépendant, sans affiliation ni approbation d'IMT Atlantique.
- Le mot de passe IMT transite en mémoire pendant l'authentification CAS mais n'est jamais stocké pour les comptes publics.
- Les données PASS et COMPETENCES restent officielles dans l'interface : elles ne sont ni ajoutées, ni modifiées, ni masquées par l'étudiant.
- Les fonctions facultatives possèdent des choix séparés. Refuser Telegram, l'agenda, l'actualisation automatique, le partage ou le classement n'empêche pas d'utiliser l'espace académique déjà importé.
- Le code source documente le logiciel, mais ne prouve ni la configuration, ni la version, ni l'intégrité d'une instance en cours d'exécution.

## Cartographie

| Périmètre | Données | Conservation et exposition |
| --- | --- | --- |
| Compte académique | Identifiant CAS, identité, campus, cursus, promotion, notes, coefficients, UE, semestres, grades et ECTS | Privé jusqu'à la suppression du compte, sauf publication ou partage explicitement activé |
| Session PASS/HUB | Cookies `Secure` filtrés pour les domaines autorisés | Chiffrés, révocables, jamais renvoyés par l'API et supprimés au plus tard après 30 jours |
| Connexion IMTégrale | Sessions web, clés publiques de passkey, préfixes et empreintes HMAC des tokens | Une passkey ne contient pas de secret serveur ; le token brut n'est montré qu'à sa création |
| Telegram | Token du bot, Chat ID, état et date du dernier test | Secrets chiffrés jusqu'au remplacement ou à la suppression du compte ; désactiver les notifications suspend l'envoi sans effacer la configuration |
| Agenda INPASS | URL iCalendar chiffrée et cache minimal des événements | Le secret n'est jamais réaffiché ni accessible à un token partagé ; le cache peut survivre à une panne de la source |
| Simulations | Copies privées d'UE, d'évaluations et d'hypothèses | Cinq scénarios GPA et cinq scénarios de notes maximum ; aucun effet sur PASS, les notifications ou le classement |
| Relevé académique | Sélection temporaire des données du compte | PDF généré en mémoire, envoyé avec `Cache-Control: no-store` puis oublié par le serveur |
| Classement | Identité PASS, GPA, moyenne, date de vérification et fraîcheur | Publication séparée auprès des participants actifs ; retrait et effacement du périmètre immédiats |
| Sauvegardes de l'instance de référence | Copie chiffrée de PostgreSQL | Rotation quotidienne et rétention de 30 jours ; une donnée supprimée peut rester dans une sauvegarde jusqu'à son expiration |

Les journaux techniques et événements d'audit dépendent du déploiement. La suppression d'un compte peut laisser une trace administrative minimale dissociée du compte actif. Un exploitant public doit définir une durée proportionnée, automatiser la purge et l'annoncer dans sa notice de confidentialité.

## Choix facultatifs

### Actualisation automatique

Elle est désactivée pour tout nouveau compte. L'étudiant choisit une fréquence de base entre deux et vingt-quatre heures, uniquement les jours ouvrés entre 8 h et 20 h, et peut retirer son accord à tout moment. Le retrait empêche tout nouveau démarrage planifié ; une requête réseau déjà engagée peut finir.

### Publication au classement

L'activation publie immédiatement l'identité et les deux scores aux participants qui ont déjà accès au classement. Le nouvel inscrit attend quarante-huit heures avant de voir une ligne, un rang ou un compteur. Il peut se retirer immédiatement et revenir sans délai ; chaque retour relance l'attente de quarante-huit heures.

### Telegram, agenda et partage

Ces trois fonctions sont indépendantes. Les deux premières ajoutent un secret chiffré nécessaire à un service externe. Le partage crée un token auquel l'étudiant attribue un rôle et, éventuellement, une expiration. Toute révocation ferme les sessions issues de ce token.

## Limite technique persistante

Même sans mot de passe stocké, les requêtes PASS et Hub partent du serveur IMTégrale. Les quotas par compte, le verrou global, le repos inter-requêtes et le circuit breaker limitent la charge et suspendent les synchronisations en cas d'instabilité.
