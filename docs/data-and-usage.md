# Données, consentements et cadre d'utilisation

Ce document décrit le comportement du logiciel IMTégrale `4.5.4`. Il aide un étudiant à comprendre le service et un exploitant à préparer son propre déploiement. Il ne remplace ni la charte informatique de l'établissement, ni une analyse juridique adaptée à l'instance réellement ouverte.

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

## Responsabilités distinctes

Le consentement concerne le traitement des données personnelles. Selon la [CNIL](https://www.cnil.fr/fr/les-bases-legales/consentement), il doit être libre, spécifique, éclairé, univoque, prouvable et aussi simple à retirer qu'à donner. Cela ne constitue pas une autorisation donnée par IMT Atlantique d'automatiser ses portails.

Le [règlement intérieur d'IMT Atlantique](https://www.imt-atlantique.fr/sites/default/files/ecole/ddrs/odd/ODD%2016/REGLEMENT-INTERIEUR-IMT%20A%202023-version-suite-CE-14-avril-2023.pdf) intègre une charte d'utilisation des ressources informatiques et subordonne l'ouverture du compte à sa signature. La [charte RENATER](https://www.renater.fr/wp-content/uploads/2022/01/charte-de-bon-usage-de-linformatique-et-du-reseau-renater.pdf), également citée par l'école, encadre un accès personnel et incessible ainsi qu'une consommation rationnelle des ressources. La version exacte de la charte IMT signée par l'étudiant doit être consultée sur l'intranet.

L'exploitant de l'instance choisit les finalités et les moyens du traitement. Avant une ouverture publique, il doit au minimum :

- identifier clairement l'exploitant et fournir un contact privé ;
- documenter une base légale pour chaque finalité ;
- annoncer les données, destinataires, durées et droits applicables ;
- permettre l'accès, la rectification lorsque la source le permet, l'effacement et le retrait des consentements ;
- tenir un registre adapté, documenter les incidents et prévoir la notification des violations lorsque le RGPD l'exige ;
- vérifier les règles de PASS, COMPETENCES, INPASS, Telegram et de son hébergeur ;
- arrêter le service ou la synchronisation si l'établissement retire son autorisation ou demande l'arrêt des appels.

## Limite technique persistante

Même sans mot de passe stocké, les requêtes PASS et Hub partent du serveur IMTégrale. Les quotas par compte, le verrou global, le repos inter-requêtes et le circuit breaker réduisent la charge ; ils ne rendent pas le trafic distribué par adresse IP et ne garantissent pas son acceptation par la DISI.
