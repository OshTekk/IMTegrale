# Identité IMTégrale

## Positionnement

**IMTégrale** est un service étudiant indépendant de suivi académique. Sa promesse publique est : **« Vos résultats, enfin réunis. »**

Le nom associe l'écosystème IMT à l'idée d'une vue intégrale des résultats. Cette proximité impose une séparation explicite avec l'identité institutionnelle.

## Attribution et indépendance

Les formulations publiques de référence sont :

- **Données importées depuis votre compte PASS** ;
- **Portail de scolarité d'IMT Atlantique** ;
- **Service étudiant indépendant, non affilié ni approuvé par IMT Atlantique.**

PASS et IMT Atlantique sont cités comme source et contexte. Les formulations « officiel », « partenaire », « certifié », « approuvé » ou « propulsé par » sont interdites sans autorisation écrite.

## Système visuel

- Le symbole est une intégrale simplifiée reliant deux points de données.
- Le mot-symbole s'écrit toujours **IMTégrale**, avec l'accent et sans espace.
- La couleur principale est un bleu pétrole `#0B4F50`, distinct du bleu officiel de l'IMT.
- Le corail `#E76F51` et le vert d'eau `#8BD3C7` sont des accents ponctuels.
- Le symbole doit rester lisible à 16 px, fonctionner sur fond clair ou sombre et conserver une version monochrome possible.

Le logo, le tangram, les couleurs exactes et les compositions officielles de l'IMT ne doivent jamais être repris ou adaptés.

## Interface mobile

La navigation d'une session propriétaire primaire conserve au maximum cinq emplacements : Accueil, Résultats, Agenda, Simuler et Plus. Plus ouvre uniquement les destinations secondaires autorisées pour la session. Une session déléguée ou viewer ne voit ni emplacement vide ni destination interdite ; cette présentation ne remplace jamais les contrôles d'autorisation du serveur.

Les interfaces tactiles réservent au moins 44 par 44 px aux commandes principales et iconographiques. La barre inférieure prend en compte `safe-area-inset-bottom`, et la structure de page réserve sa hauteur plus une marge de respiration afin qu'aucune action ne soit masquée. Les modales prennent en compte les zones sûres haute et basse.

Le thème sombre conserve les mêmes niveaux de hiérarchie et de contraste que le thème clair. Avec `prefers-reduced-motion: reduce`, le comportement global supprime les animations décoratives, les transitions de vue et le défilement animé, y compris pour les feuilles chargées avec une route paresseuse.

## Compatibilité technique

Les contrats internes existants restent inchangés : paquet et commande `botnote`, variables `BOTNOTE_*`, chemin `/opt/botnote`, unités systemd `botnote-*`, événements frontend `botnote:*` et en-tête proxy `X-BotNote-Client-Identity`. Ils ne doivent pas apparaître comme nom public du produit.
