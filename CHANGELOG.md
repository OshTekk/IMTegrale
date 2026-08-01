# Changements

## Non publié

- backend et interface des comparaisons privées V1, entièrement fermés par
  feature flag : invitations one-shot, consentement bilatéral, révocation,
  expiration et intersection exacte des UE officielles ;
- migration additive `0029`, sans donnée académique copiée, avec backfill de
  curseurs événementiels aléatoires et toujours non déployée dans ce lot ;
- espace lazy `/comparisons`, navigation conditionnelle, traitement mémoire du
  fragment d'invitation, listes et détail bilatéral sans intégration au
  leaderboard ;
- contrats OpenAPI, tests IDOR, concurrence, confidentialité, cache, responsive
  et Axe, sans activation ni déploiement.
- correction C2 des invitations antérieures à un cycle terminé : borne
  terminale stricte, bearer obsolète invalidé et courses PostgreSQL sérialisées.
- consentement Comparaisons privées V2 issu d'un manifeste backend canonique :
  champs inclus et catégories exclues exhaustifs, création et acceptation
  fermées en cas d'indisponibilité ou de divergence.
- bearer d'invitation one-shot lié au scope opaque de la session primaire,
  réponses tardives rejetées, purge sur changement de principal ou BFCache et
  titre de document générique.
- frontière centrale de session vérifiée : deadline monotone, purge synchrone
  du DOM et des caches, révocation inter-onglets et échec fermé hors ligne.
- remédiation C6A : `SessionAuthority` document-scoped au-dessus du routeur,
  QueryClient recréé pour chaque `auth_epoch`, observateur inter-onglets
  permanent et requêtes de session séquencées.
- fragment d'invitation capturé et scrubbed au bootstrap avant toute
  autorisation, capacité volatile one-shot liée au scope vérifié et détruite
  pour tout principal ou état non autorisé.
- deadline de session conservatrice avec RTT intégral soustrait et interdiction
  de prolonger un même scope ; fermeture synchronique à l'expiration, offline,
  `pagehide` et au retour BFCache.
- binding `X-IMTEGRALE-SESSION-BINDING` obligatoire dans le contrat OpenAPI et
  le client généré ; `WebSession`, compte, rôle, méthode, génération et
  capacité relus sous verrou avant tout effet durable.
- ordre total des verrous Comparaisons : `WebSession`, comptes participants
  triés, invitation puis relation, avec courses PostgreSQL sans deadlock.
- lease frontend explicite et événements terminaux minimaux envoyés aux deux
  participants ; DOM, QueryCache, MutationCache, preview et bearers purgés
  avant tout refetch.
- historique terminal réduit au statut et à la date de fin, sans identité ni
  résultat vivant ; décisions relationnelles et comptes relus fraîchement sous
  verrou avant toute lecture ou mutation.
- événements Comparaisons réservés au propriétaire primaire dans le dashboard,
  avec curseurs aléatoires opaques pour le dashboard et le flux SSE ; aucun ID
  global ou trou de séquence n'est exposé à un token `owner`.
- scanner de secrets fail-closed et en streaming, y compris au-delà de cinq
  Mio et dans les archives de publication, avec comptage explicite des fichiers
  rejetés ou non scannés.
- C6A laisse explicitement à C6B la pseudo-révocation d'éligibilité, l'oracle
  de rétention et la copy actor-specific, et à C6C les constats ZIP, binaires,
  Telegram et snapshot ; flag toujours faux et migration `0029` non déployée.
- remédiation supply-chain C6C : parser ZIP brut et sémantique couvrant EOCD,
  ZIP64, headers locaux/centraux, descriptors, noms, extras, commentaires,
  contenus compressés/décompressés et archives imbriquées, avec comptabilité
  `archive_regions_unscanned=0` ;
- politique binaire fail-closed : dix PNG suivis et 59 fontes KaTeX liés à
  SHA-256, taille, type, provenance et chemins exacts ; aucun format autorisé
  uniquement par extension ou magic, aucune entrée active inutilisée ;
- exemptions Telegram liées au digest de la valeur synthétique complète, à la
  règle, au chemin de test, au purpose et au nombre d'occurrences, sans
  suppression contextuelle ;
- capsule de release v1 déterministe et content-addressed, construite depuis
  des descripteurs stables ; tous les contrôles, l'upload et le round-trip
  consomment le même fichier avec son SHA-256 attendu, sans reparcours du build ;
- harness de mutation C6C tuant les dix régressions supply-chain exigées dans
  des copies temporaires, benchmark scanner documenté et bootstrap CI de
  `pip 26.1.2` audité ; l'horloge de la fixture Agenda est figée pour supprimer
  sa dépendance à la date réelle, sans changement fonctionnel.

## 4.8.0 - 23 juillet 2026

- espace unique **Résultats** avec vues Par UE, Évaluations et Nouveautés ;
- filtres, tris et état de lecture conservés dans l'URL, avec deep links par UE ;
- redirections rétrocompatibles de `/notes` et `/ues`, sans changement pour `/ues/releve` ;
- remplacement de la table académique large par des cartes adaptées du téléphone au desktop ;
- libellé explicite des dates d'import, sans les présenter comme des dates d'évaluation ;
- couverture fictive de 100 UE et 500 évaluations, huit viewports, clavier, thèmes et Axe.

## 4.7.0 - 22 juillet 2026

- schéma Parcours v3 rétrocompatible et nouveau mode fermé `personal_library` ;
- droits explicites séparant usage personnel, consultation inline et téléchargement ;
- autorisation des assets par action avant ouverture, y compris pour les accès directs par ID ;
- extraits de recherche lecteur explicites, indépendants du corpus d'indexation ;
- menu secondaire de vérification et interface PDF respectant les capacités de téléchargement ;
- fixtures intégralement fictives et tests de séparation compte, ingress, session, droits, Range et recherche.

## 4.6.0 - 21 juillet 2026

- nouvelle expérience éditoriale Parcours : accueil, reprise, modules structurés, glossaire et documents séparés ;
- mode lecteur sans métadonnées de fabrication et mode Revue facultatif réservé au propriétaire primaire ;
- schéma de bundle v2 avec compatibilité v1 et guide de migration public ;
- rendu mathématique KaTeX strict, accessible et auto-hébergé ;
- lecteur PDF.js local chargé à la demande et support HTTP Range authentifié ;
- fixtures exclusivement synthétiques, tests responsive, clavier, reduced-motion et Axe, avec budgets distincts pour le graphe initial et PDF.js.

## 4.5.6 - 19 juillet 2026

- retrait du bloc éditorial « Consentement et cadre d'usage » du README et de la page Confiance ;
- documentation publique recentrée sur le fonctionnement technique, les choix utilisateur et l'effacement des données.

## 4.5.5 - 19 juillet 2026

- rendu de toutes les modales dans un portail racine afin qu'elles couvrent réellement la navigation mobile et les autres contextes d'empilement.

## 4.5.4 - 18 juillet 2026

- correction de la sheet de détail d'un cours sur mobile : hauteur dynamique, safe area et défilement tactile jusqu'au lieu.

## 4.5.3 - 18 juillet 2026

- correction du filtre de semestres sur mobile : libellé compact, boutons non compressibles et défilement horizontal jusqu'à S10.

## 4.5.2 - 18 juillet 2026

- accueil et page Confiance alignés sur le modèle sans mot de passe IMT stocké ;
- démo étendue aux notes et ECTS, accès, simulations, agendas, relevé PDF, Telegram et classement ;
- distinction explicite entre consentement aux traitements et règles d'utilisation du SI IMT ;
- cartographie publique des données, secrets, choix facultatifs et limites d'exploitation ;
- politique de sécurité corrigée pour décrire les sessions PASS/HUB chiffrées limitées à 30 jours.

## 4.5.1 - 18 juillet 2026

- suppression physique des anciens mots de passe IMT en base ;
- réutilisation d'une session PASS/HUB filtrée et chiffrée, avec reconnexion volontaire à son expiration ;
- observatoire privé des sessions et de leur longévité pour la phase bêta de 30 jours.

## 4.4.0 - 18 juillet 2026

- génération d'un relevé académique personnel non officiel, sans stockage serveur.

## 4.3.0 - 18 juillet 2026

- agenda INPASS personnel et calendriers de formation FIP 2027 à 2029.
