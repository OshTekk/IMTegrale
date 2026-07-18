# Simulations GPA

L'espace **Simulations** permet au propriétaire d'un compte de préparer jusqu'à cinq projections privées. Un scénario peut partir d'une page vide ou d'une copie des UE connues par IMTégrale. Cette copie est ensuite entièrement modifiable sans changer les données PASS ou COMPETENCES.

## Calcul

La règle produit `gpa-ects-v1` utilise le barème suivant : `A = 4`, `B = 3,8`, `C = 3,5`, `D = 3`, `E = 2,5`, `FX = 0` et `F = 0`.

Le GPA simulé est calculé avec la formule :

```text
somme(points GPA x ECTS) / somme(ECTS)
```

Le moteur emploie une arithmétique décimale et un arrondi au centième, demi-supérieur. Une UE sans grade reste en attente. Une UE gradée sans ECTS reste visible mais est exclue du calcul. Le résultat est une projection IMTégrale non officielle et n'alimente jamais le leaderboard.

## Source et hypothèses

Chaque UE importée conserve séparément :

- sa valeur de référence, sa provenance et sa date d'observation ;
- la valeur actuellement utilisée par le scénario ;
- son état de source : courante, modifiée ou indisponible.

Modifier une UE importée transforme uniquement la copie en hypothèse. Lorsqu'une synchronisation académique fait évoluer la source, le scénario n'est jamais modifié silencieusement. Un rebasage explicite met à jour les lignes intactes, conserve les hypothèses et demande une résolution lorsque les deux versions divergent.

Un scénario peut être renommé, dupliqué, réinitialisé, comparé ou supprimé. Les modifications valides sont enregistrées automatiquement sur le serveur. Un numéro de version empêche deux onglets de s'écraser ; l'utilisateur peut recharger la version distante ou préserver ses changements dans une nouvelle copie.

## Confidentialité

Les scénarios sont liés au compte propriétaire et contrôlés côté serveur à chaque requête. Les sessions ouvertes par token de partage sont refusées, même lorsqu'un ancien token possède un rôle propriétaire. Les événements de simulation sont également retirés du tableau de bord et du flux SSE d'une session par token.

Les événements techniques enregistrent uniquement l'identifiant du scénario et le type d'action. Ils ne contiennent ni grade, ni ECTS, ni intitulé d'UE. Supprimer le compte supprime ses scénarios et leurs entrées par cascade.

## Limites

- cinq scénarios par compte ;
- 120 UE par scénario ;
- semestres `S1` à `S10` ;
- ECTS strictement positifs, avec un maximum de 60 par UE ;
- aucune exportation et aucun envoi vers PASS ou COMPETENCES.
