# Relevé académique personnel

La page **UE & ECTS > Relevé académique** permet au propriétaire du compte de générer un PDF A4 à partager dans le cadre d'une candidature, d'un entretien ou d'un suivi personnel. Le document reste volontairement présenté comme un **relevé personnel à titre informatif** : il n'est ni édité, ni certifié, ni validé par IMT Atlantique et ne remplace pas un relevé officiel.

## Contenu

Le relevé peut couvrir tous les semestres disponibles ou un semestre précis de `S5` à `S10`. L'étudiant choisit d'inclure ou non son identité PASS et l'annexe détaillée des évaluations.

Le PDF contient :

- l'identité et le profil académique synchronisés depuis PASS lorsqu'ils sont disponibles ;
- les intitulés d'UE, semestres, grades et ECTS disponibles dans COMPETENCES ;
- les évaluations et coefficients synchronisés depuis PASS ;
- les moyennes, GPA, regroupements et états calculés par IMTégrale ;
- les dates de dernière synchronisation de chaque source ;
- une explication explicite de la provenance et du statut non officiel du document.

Les simulations et les tokens de partage ne sont jamais inclus. Lorsqu'un grade COMPETENCES n'est pas disponible, le grade calculé depuis la moyenne PASS est clairement marqué comme tel.

## Transparence

PASS, COMPETENCES et le dépôt GitHub sont liés directement dans le PDF. Un QR code renvoie également vers le code source d'IMTégrale. Un destinataire peut ainsi consulter le fonctionnement de l'import, les règles de calcul et les limites du service.

Ce lien public apporte de la transparence, mais ne constitue ni une signature, ni une preuve indépendante de l'authenticité du PDF. Seul IMT Atlantique peut délivrer ou certifier un relevé officiel. Cette limite est affichée dans le document afin qu'un PDF IMTégrale ne puisse pas raisonnablement être confondu avec un document administratif de l'école.

## Confidentialité

Le PDF est assemblé en mémoire à la demande et n'est pas enregistré sur le serveur. La réponse porte `Cache-Control: no-store` et n'est accessible qu'à une session propriétaire ; un token de partage reçoit `403`. L'étudiant peut produire une version anonymisée avant de la transmettre.

La génération est limitée à huit documents par minute et par compte. Le nom du fichier ne contient qu'une version normalisée de l'identité choisie et la date de génération.

## API

```text
GET /api/v1/academic-reports/personal.pdf
```

Paramètres :

- `semester=all|S5|S6|S7|S8|S9|S10` ;
- `include_assessments=true|false` ;
- `include_identity=true|false`.
