# Agendas

IMTégrale distingue deux sources qui n'ont ni la même portée ni le même niveau de confidentialité.

## Agenda de cours INPASS

Chaque propriétaire peut enregistrer son lien privé `https://inpass.imt-atlantique.fr/passcal/getics`. Le serveur refuse tout autre schéma, hôte, port, chemin, redirection ou paramètre, puis vérifie que le login `@imta.fr` correspond à l'identifiant du compte. Une résolution DNS non publique est également refusée.

Le lien normalisé est chiffré par AES-256-GCM avec un contexte lié à l'identifiant du compte. Seuls son empreinte non réversible et un login masqué sont exposés au reste de l'application. Il n'apparaît ni dans les réponses API, ni dans les événements d'audit. Un lien ne peut être rattaché qu'à un compte.

Les routes sont réservées à la session principale du propriétaire. Les sessions issues d'un token de partage reçoivent `403`, quel que soit leur rôle. Supprimer l'agenda efface dans la même transaction le secret chiffré et tous les événements mis en cache.

## Synchronisation

La connexion déclenche un premier import, limité à trois tentatives par heure et à une tentative par minute pour un même compte. Les téléchargements sortants sont sérialisés et espacés. Aucune redirection n'est suivie, les proxys d'environnement sont ignorés, les délais réseau et la taille du corps sont bornés.

L'ordonnanceur systemd ajoute ensuite un job PostgreSQL échu chaque heure ; le worker calendrier le réclame avec un bail et une clé d'idempotence propres à cette échéance. Il utilise `ETag` et `Last-Modified` lorsque le serveur les fournit. Une erreur conserve les derniers cours valides et planifie une nouvelle tentative une heure plus tard ; elle ne provoque pas de boucle rapide.

Le parseur conserve uniquement le titre, le lieu, les bornes temporelles et le caractère journée entière. Les descriptions, commentaires, organisateurs, participants et métadonnées de calendrier sont ignorés. L'expansion est limitée à la fenêtre comprise entre 400 jours dans le passé et 730 jours dans le futur, avec 5 000 événements maximum. Les récurrences horaires ou plus fréquentes sont refusées.

## Calendrier de formation FIP

Le calendrier 2026-2027 est une transcription structurée du document IMT Atlantique / ITII Bretagne, version du 28 avril 2026. Il couvre les promotions FIP 2027, 2028 et 2029. Tous les comptes dont le cursus officiel est `FIP` peuvent sélectionner les trois promotions.

Les périodes école, entreprise, semestres, volumes hebdomadaires et mobilités reprennent le document source. Rennes et Brest ne sont affichés que sur les périodes où le campus est écrit explicitement ; aucune localisation n'est inférée.

## Vérification opérationnelle

Avant chaque release :

1. exécuter `pytest`, le typecheck, les tests frontend et les audits de dépendances ;
2. vérifier la migration `0016` sur une copie de la base après sauvegarde chiffrée ;
3. connecter un flux de test, contrôler que sa valeur en clair n'est présente ni en base, ni dans les journaux, ni dans l'API ;
4. vérifier une réponse conditionnelle ou un flux inchangé, puis une indisponibilité temporaire sans perte du cache ;
5. confirmer qu'une session par token ne peut atteindre aucune route `/api/v1/calendar`.
