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
