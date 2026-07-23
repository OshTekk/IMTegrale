# Résultats

L'espace **Résultats** réunit les anciennes pages Notes et UE & ECTS. Il utilise une seule lecture du snapshot dashboard et ne recalcule aucune valeur académique dans React.

## Routes

- `/results?view=ues` : unités d'enseignement, regroupées par année et semestre ;
- `/results?view=evaluations` : recherche, filtres et tris des évaluations ;
- `/results?view=recent` : derniers résultats importés, par date de détection ;
- `/results/ue/:ueCode` : détail partageable d'une UE ;
- `/ues/releve` : relevé académique personnel, inchangé.

Les anciennes routes restent compatibles :

- `/notes` redirige vers `view=evaluations` ;
- `/ues` redirige vers `view=ues`.

Ces redirections remplacent l'entrée d'historique devenue obsolète et préservent les paramètres encore compris par Résultats.

## Contrat de données

Moyenne, GPA, grade, validation, ECTS et état de rattrapage sont calculés par le backend. Le frontend ne fait que construire des index, filtrer, grouper et trier les réponses.

Le parseur PASS refuse plus de `2 000` évaluations actives pour un compte. Le snapshot dashboard en accepte `2 500` ; il est donc complet pour le contrat actuel. Un test impose que cette seconde limite ne puisse pas devenir inférieure à la première.

`detected_at` est la date à laquelle IMTégrale a découvert l'évaluation. L'interface utilise **Importée le** et ne la présente jamais comme la date de l'examen. La vue Nouveautés est la seule à appliquer cet ordre par défaut.

## État de navigation

Les paramètres utiles sont `view`, `year`, `semester`, `ue`, `type`, `sort` et `q`. Une valeur inconnue est ignorée ou remplacée par la valeur sûre par défaut. Aucun paramètre ne contient de donnée secrète.

Les permissions restent celles des routes existantes : propriétaire et viewer peuvent consulter les résultats ; le relevé académique reste réservé à une session propriétaire primaire.
