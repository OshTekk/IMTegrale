# Simulations privées

L'espace **Simulations** contient deux outils séparés : les projections de GPA et les projections de notes. Chaque outil autorise jusqu'à cinq scénarios privés. Un scénario peut partir d'une page vide ou d'une copie des données connues par IMTégrale. Cette copie est ensuite entièrement modifiable sans changer les données PASS ou COMPETENCES.

## Projection GPA

La règle produit `gpa-ects-v1` utilise le barème suivant : `A = 4`, `B = 3,8`, `C = 3,5`, `D = 3`, `E = 2,5`, `FX = 0` et `F = 0`.

Le GPA simulé est calculé avec la formule :

```text
somme(points GPA x ECTS) / somme(ECTS)
```

Le moteur emploie une arithmétique décimale et un arrondi au centième, demi-supérieur. Une UE sans grade reste en attente. Une UE gradée sans ECTS reste visible mais est exclue du calcul. Le résultat est une projection IMTégrale non officielle et n'alimente jamais le leaderboard.

## Projection de notes

La règle `notes-weighted-v1` calcule d'abord chaque moyenne d'UE :

```text
somme(note sur 20 x coefficient) / somme(coefficients)
```

La moyenne générale simulée pondère ensuite les moyennes d'UE par leurs crédits :

```text
somme(moyenne UE x ECTS) / somme(ECTS)
```

Le grade potentiel de chaque UE est dérivé de sa moyenne. Le GPA secondaire est alors pondéré par ECTS avec le même barème que le simulateur GPA. Une note vide reste en attente et n'est jamais remplacée par zéro. Lorsqu'une ou plusieurs notes de rattrapage sont renseignées, la dernière note de rattrapage du scénario remplace la moyenne normale ; une note de rattrapage supérieure ou égale à 10 produit le grade E et 2,5 points GPA.

Une UE non validée n'est pas masquée du calcul : si elle dispose d'une moyenne et d'ECTS, sa moyenne participe à la moyenne générale et son grade `FX` ou `F` apporte temporairement 0 point au GPA dérivé. Une future réussite au rattrapage remplace ce résultat dans le scénario.

L'import copie les évaluations PASS avec leurs coefficients ainsi que les intitulés, semestres et ECTS COMPETENCES. L'étudiant peut ensuite modifier cette copie, ajouter des UE futures et filtrer les résultats par semestre.

## Source et hypothèses

Chaque UE ou évaluation importée conserve séparément :

- sa valeur de référence, sa provenance et sa date d'observation ;
- la valeur actuellement utilisée par le scénario ;
- sa nature : officielle importée, hypothèse modifiée ou valeur simulée ;
- son état de source : courante, en conflit ou indisponible.

Modifier une valeur importée transforme uniquement la copie en hypothèse. Lorsqu'une synchronisation académique fait évoluer la source, le scénario n'est jamais modifié silencieusement. Un rebasage explicite met à jour les lignes intactes, conserve les hypothèses et demande une résolution au niveau de l'UE ou de l'évaluation lorsque les deux versions divergent.

Un scénario peut être renommé, dupliqué, réinitialisé, comparé ou supprimé. Les modifications valides sont enregistrées automatiquement sur le serveur. Un numéro de version empêche deux onglets de s'écraser ; l'utilisateur peut recharger la version distante ou préserver ses changements dans une nouvelle copie.

## Ergonomie de la projection de notes

L'éditeur de notes suit une progression stable : scénario, synthèse, semestre, UE, puis évaluation. Les UE sont repliées au chargement, sauf lorsqu'un conflit doit être traité. Leur contenu n'est monté dans le DOM qu'à l'ouverture. Sur téléphone et tablette, ouvrir une UE referme la précédente ; sur ordinateur, plusieurs UE peuvent rester ouvertes et une action permet de tout replier.

Une évaluation fermée reste une ligne de synthèse. Son édition utilise un formulaire temporaire dans une feuille plein écran sur téléphone : annuler ne modifie pas le scénario, tandis qu'appliquer reporte la valeur dans le brouillon principal puis laisse l'autosauvegarde existante agir. Le même principe s'applique à l'ajout d'une UE ou d'une évaluation afin de ne jamais créer de ligne vide après une annulation.

Le scénario actif est mémorisé localement sans donnée académique. La sélection, le filtre de semestre, l'UE ouverte, le scroll et le focus restent indépendants de l'état serveur. Une réponse d'autosauvegarde met à jour les identifiants et la version sans remonter la page ni rouvrir toutes les UE. Après une feuille ou une confirmation, le focus revient à l'action d'origine.

Le résumé est calculé immédiatement depuis le brouillon avec les fonctions pures communes. Cette mise à jour locale ne change ni les formules, ni les payloads API, ni le délai d'autosauvegarde, ni le contrôle de version optimiste. Les états `Modifications locales`, `Enregistrement…`, `Enregistré`, `Action requise` et `Échec, réessayer` sont annoncés sans dépendre uniquement de la couleur.

## Ergonomie de la projection GPA

L'éditeur GPA reprend la même progression : scénario, synthèse, semestre, puis UE. Le mode d'édition dépend de la largeur réelle du workspace, après déduction de la navigation, et non de la seule largeur du viewport. Un conteneur étroit présente des cartes d'UE repliées et n'insère aucun formulaire dans le DOM tant qu'une UE n'est pas choisie. Un conteneur large utilise une liste et un éditeur adjacent afin de conserver une lecture dense sans comprimer les champs.

Sur téléphone et tablette, l'édition d'une UE s'effectue dans une feuille avec un brouillon temporaire. Annuler ne change pas le scénario ; appliquer reporte les valeurs dans le brouillon principal, recalcule immédiatement le GPA et laisse l'autosauvegarde existante agir. Le corps de la feuille défile indépendamment de ses actions afin que celles-ci restent accessibles lorsque le clavier virtuel réduit la hauteur visible.

Le filtre de semestre ne modifie jamais les données. Une nouvelle UE reprend le semestre actif, puis sa carte reçoit le focus sans saut de scroll. Les conflits et sources indisponibles restent visibles sur les cartes fermées. La résolution compare toujours la valeur officielle et l'hypothèse ; elle n'est jamais automatique.

## Confidentialité

Les scénarios sont liés au compte propriétaire et contrôlés côté serveur à chaque requête. Les sessions ouvertes par token de partage sont refusées, même lorsqu'un ancien token possède un rôle propriétaire. Les événements de simulation sont également retirés du tableau de bord et du flux SSE d'une session par token.

Les événements techniques enregistrent uniquement l'identifiant du scénario et le type d'action. Ils ne contiennent ni grade, ni note, ni ECTS, ni intitulé d'UE. Supprimer le compte supprime ses scénarios, UE et évaluations par cascade.

## Semestres

IMTégrale affiche exclusivement les semestres du cursus ingénieur selon la convention académique globale : `S5` à `S10`, dans les vues, filtres, calculs, simulations et réponses API. Lorsque COMPETENCES fournit sa numérotation interne de `1` à `6`, la valeur brute reste confinée à la couche d'import pour la traçabilité puis est convertie en `S5` à `S10`. Un semestre présent dans l'intitulé officiel de l'UE sert de contrôle ; une contradiction fait échouer l'import au lieu d'enregistrer une donnée ambiguë.

## Limites

- cinq scénarios GPA et cinq scénarios de notes par compte ;
- 120 UE par scénario ;
- 60 évaluations par UE et 600 évaluations par scénario de notes ;
- semestres `S5` à `S10` ;
- ECTS strictement positifs, avec un maximum de 60 par UE ;
- notes comprises entre 0 et 20 et coefficients strictement positifs, avec un maximum de 100 ;
- aucune exportation et aucun envoi vers PASS ou COMPETENCES.
