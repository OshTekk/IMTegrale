# Rollout de la synchronisation autonome

## Portée

G7 est séparé en trois décisions :

| Gate | Portée | État |
| --- | --- | --- |
| G7A | Produit, consentement, activation et mécanisme de rollout | Terminé, production fermée |
| G7B | Canary d'un seul propriétaire primaire | Non commencé |
| G7C | Ouverture contrôlée après observation | Non commencé |

G7A n'autorise aucun mot de passe réel, aucun compte autonome et aucun appel
PASS pendant le déploiement. Les parcours canary sont validés uniquement avec
des comptes, clés, sessions et credentials synthétiques.

## Configuration

Le rollout repose sur quatre réglages privés :

```text
BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT=off|canary|all
BOTNOTE_AUTONOMOUS_SYNC_ENABLED=false|true
BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED=false|true
BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS='["UUID interne canonique"]'
```

Les identifiants canary sont des UUID internes canoniques, dédupliqués et
limités à 25. Aucun login IMT, nom ou identifiant étudiant ne doit apparaître
dans Git, les logs ou l'API. La valeur d'environnement est une liste JSON :
`[]` lorsqu'elle est vide, ou `["uuid-1","uuid-2"]` dans la configuration
privée d'un futur canary.

| Rollout | Runtime | Enrôlement | Allowlist | Résultat |
| --- | --- | --- | --- | --- |
| `off` | `false` | `false` | vide | Configuration fermée valide |
| `off` | autre | autre | non vide | Démarrage production refusé |
| `canary` | `true` | `true` | 1 à 25 UUID | Comptes explicitement autorisés seulement |
| `canary` | autre | autre | vide | Démarrage production refusé |
| `all` | `true` | `true` | vide | Tous les propriétaires primaires éligibles |
| `all` | autre | autre | non vide | Démarrage production refusé |

La production G7A conserve exclusivement la première ligne. La valeur `all`
est implémentée et testée, mais interdite par la procédure G7A et G7B.

## Décision serveur

`autonomous_sync_available_for()` retourne vrai seulement si :

- le compte existe et reste actif ;
- la session est un propriétaire primaire obtenu par IMT ou passkey ;
- aucun token partagé n'est lié à la session ;
- runtime et enrôlement sont activés ;
- le rollout autorise l'identifiant interne exact ;
- le heartbeat récent du worker confirme le profil isolé, l'identité dédiée,
  l'opener credential, les keysets et le stockage HPKE ;
- le web possède le sealer public credential attendu.

Aucune valeur envoyée par React ne prouve le rollout. Un viewer ou un token
owner reçoit une vue neutre : aucun état du credential, aucune preuve de
canary, aucun détail du worker.

Lorsque le rollout est actif, `/health/ready` ne peut pas être vert si le
worker autonome n'est pas prêt. `/health/live` reste indépendant de cette
capacité.

Les erreurs publiques restent bornées :

- `AUTONOMOUS_SYNC_UNAVAILABLE` pour un compte hors rollout ;
- `AUTONOMOUS_SYNC_TEMPORARILY_UNAVAILABLE` lorsque la capacité autorisée est
  momentanément indisponible ;
- `SYNC_CREDENTIAL_REQUIRED` lorsqu'aucun credential actif n'existe ;
- `SYNC_CREDENTIAL_REENROLLMENT_REQUIRED` lorsque le credential doit être
  renouvelé.

Elles ne révèlent ni allowlist, ni unité systemd, ni clé, ni génération.

## Activation et révocation

L'enrôlement vérifie le mot de passe, renouvelle la session PASS/HUB puis
scelle le credential. Il n'active pas le mode. L'activation ultérieure :

- exige à nouveau une session propriétaire primaire, Origin et CSRF ;
- valide rollout, readiness et credential ;
- écrit le mode et la planification ;
- ne déchiffre rien ;
- ne lance ni job immédiat, ni SSO, ni notification.

Le scheduler et le worker revérifient le rollout indépendamment de l'API. Une
ligne `autonomous` injectée hors allowlist est mise en pause sans lecture du
credential et sans appel PASS.

Une transition vers `manual` ou `session_only` révoque l'enveloppe dans le
même commit. Les anciennes routes booléennes passent également par cette
révocation. La purge PASS/HUB révoque credential et sessions techniques, coupe
la planification et revient à `manual`.

## Activation incomplète

Après un enrôlement réussi mais avant l'activation, l'API expose
`activation_pending=true` uniquement au propriétaire primaire. Le credential
ne peut pas être utilisé tant que le compte n'est pas en mode autonome.

L'utilisateur peut :

- terminer l'activation sans nouvelle vérification si le credential reste
  actif et compatible ;
- remplacer le credential ;
- le supprimer ;
- purger tout accès PASS/HUB.

## Observabilité

Le portail administrateur privé peut afficher uniquement des agrégats :

- comptes autorisés par le rollout ;
- comptes autonomes actifs ;
- credentials actifs ou invalides ;
- opérations credential ;
- succès, erreurs d'authentification et erreurs transitoires ;
- sessions recréées et réutilisées ;
- pauses ;
- heure agrégée du dernier événement.

Avec un seul canary, ces agrégats peuvent indirectement décrire une personne.
Ils restent donc privés, sans login, sans account ID, sans contenu académique
et avec une précision temporelle limitée.

## Procédure G7B

G7B est une intervention séparée. Avant toute modification :

1. sauvegarder PostgreSQL de manière chiffrée et tester sa restauration ;
2. vérifier zéro credential réel et zéro compte autonome ;
3. vérifier keysets actifs, heartbeat et `operations-check` ;
4. choisir un seul UUID interne hors dépôt et hors logs ;
5. mettre `rollout=canary`, runtime et enrôlement à `true` dans les fichiers
   privés ;
6. fournir au web uniquement la clé publique credential prévue ;
7. ne modifier aucune clé privée ni l'isolation du worker ;
8. redémarrer, exiger une readiness verte et vérifier les vues neutres ;
9. laisser le propriétaire s'enrôler lui-même dans son navigateur ;
10. observer les agrégats privés et documenter tout incident.

Un opérateur ou administrateur ne peut pas enrôler le mot de passe à la place
du propriétaire. Toute anomalie provoque un retour immédiat à `off`, une
révocation locale du credential et la conservation des données académiques.

## Décision G7C

Le rollout `all` ne peut être envisagé qu'après une durée d'observation définie
dans G7B, sans erreur de confidentialité, de révocation, de concurrence ou de
rotation. La décision doit réévaluer :

- taux de succès des sessions recréées ;
- erreurs d'authentification et invalidations ;
- comportement après changement de mot de passe ;
- disponibilité du worker et des keysets ;
- procédure de support et de purge ;
- capacité opérationnelle à traiter un incident.

L'absence d'incident pendant un canary ne constitue pas une garantie de
sécurité.

## Rollback

Le rollback G7A est applicatif et direct : aucun schéma ni format
cryptographique ne change. Rebasculer vers la release G6B avec la base `0028`
et les keysets v2, tout en conservant rollout, runtime et enrôlement fermés.

Pour un futur rollback G7B :

1. passer le rollout à `off` et couper runtime/enrôlement ;
2. arrêter scheduler et worker selon la procédure d'incident ;
3. révoquer hors réseau tout credential actif ;
4. vérifier les agrégats à zéro ;
5. retirer la clé publique credential du web ;
6. seulement ensuite rebasculer l'application.

Une sauvegarde contenant une enveloppe doit rester protégée. Après
restauration, toutes les sessions et tous les credentials restaurés sont
révoqués avant tout redémarrage réseau.

## Risques résiduels

- Le mot de passe existe brièvement dans le navigateur et le web pendant
  l'enrôlement.
- Une RCE active à cet instant peut le capturer.
- Le worker sync peut ouvrir les credentials actifs.
- Root sur le LXC compromet les clés et la mémoire.
- Python et JavaScript ne garantissent pas la zéroisation.
- Une requête déjà envoyée ne peut pas être rappelée après une révocation.
- HPKE base n'authentifie pas l'émetteur ; les contrôles applicatifs fournissent
  consentement, contexte, génération et autorisation.

Ces limites doivent rester visibles dans le consentement et la documentation.
