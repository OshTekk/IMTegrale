# Cycle de vie du credential IMT

## État G5

La migration additive `0027` a durci le cycle de vie avant sa première
écriture. G5 ajoute les opérations serveur, mais l'enrôlement reste fermé en
production et `autonomous` demeure inexécutable. La production attend donc
toujours :

```text
active = 0
invalid = 0
revoked = 0
autonomous = 0
```

Le worker sync n'interroge pas cette table et son rôle PostgreSQL reste
explicitement refusé. La clé privée qu'il possède depuis G3 ne suffit donc pas
à créer un chemin autonome.

## Invariants 0027

Une ligne `active` contient exactement une enveloppe HPKE credential v1 de
3 172 octets, une version positive, un identifiant de clé SHA-256 hexadécimal
minuscule de 64 caractères, une génération positive, un consentement versionné
et deux dates distinctes de consentement et de vérification. Elle ne possède
aucune information de révocation.

Une ligne `invalid` ou `revoked` ne conserve ni enveloppe, ni version
d'enveloppe, ni identifiant de clé. Elle conserve la génération monotone, la
version de consentement, les dates non secrètes et une raison issue de
l'allowlist :

- `user_revoked` ;
- `manual_mode` ;
- `session_only_mode` ;
- `pass_access_purged` ;
- `credential_replaced` ;
- `credential_invalid` ;
- `key_unavailable` ;
- `database_restored` ;
- `account_disabled` ;
- `login_changed` ;
- `operator_revoked`.

La relation reste one-to-one et disparaît avec le compte. Les seuls index
secondaires portent sur l'état et l'identifiant de clé afin de permettre les
inventaires agrégés et une future rotation. L'enveloppe n'est jamais indexée.

## Consentement et enrôlement

Le consentement initial est la version `1`. Le payload exige trois
acquittements littéraux : conservation chiffrée, risque du futur worker et
suppression irréversible. Le mot de passe est un `SecretStr`, borné à 512
caractères et 2 048 octets UTF-8. Il est vérifié une seule fois avec le login
IMT déjà lié au compte, sans importer notes, UE ou ECTS.

L'appel distant se déroule sans verrou SQL. Après une vérification réussie, la
transaction finale :

1. reverrouille le compte et vérifie son activité et son login ;
2. calcule la nouvelle génération ;
3. scelle le secret avec la clé publique et le contexte compte, login,
   génération et consentement ;
4. remplace sur la même ligne l'éventuel credential précédent ;
5. remplace la session PASS/HUB par une enveloppe HPKE session ;
6. met à jour uniquement le profil vérifié ;
7. écrit des événements sans donnée cryptographique.

Le remplacement augmente la génération. Une erreur de vérification, de
scellement, de stockage de session ou de commit conserve intégralement l'ancien
credential et l'ancienne session. L'enrôlement ne change jamais le mode de
synchronisation.

## Révocation et purge

La révocation est locale, immédiate et idempotente. Elle met à `null`
l'enveloppe, sa version et son `key_id`, augmente la génération, fixe l'état et
une raison autorisée, puis commit sans appel PASS.

`DELETE /api/v1/settings/sync-credential` conserve les cookies PASS/HUB. Les
transitions explicites vers `manual` ou `session_only`, y compris par les
anciennes routes compatibles, révoquent également tout credential actif.

`POST /api/v1/settings/pass-access/purge` effectue atomiquement la révocation du
credential, la révocation des sessions techniques et le passage en `manual`.
Cette purge ne supprime ni le compte, ni les notes, ni le profil, ni les
passkeys, ni la session web courante.

La commande hors réseau
`botnote sync-credentials-revoke-all --reason <database_restored|operator_revoked>`
produit uniquement les agrégats `active_found`, `revoked`,
`already_inactive` et `affected_accounts`. L'écriture exige
`--confirm REVOKE-ALL-SYNC-CREDENTIALS`; `--dry-run` ne modifie rien.

## Confidentialité

Le web ne possède qu'un sealer public et aucune méthode d'ouverture. Les vues
API n'exposent ni enveloppe, ni `key_id`, ni génération, ni compteur d'échec,
ni taille. Les viewers et tokens owner reçoivent une vue neutre et ne
provoquent pas de lecture de la ligne credential.

Les événements, métriques et sorties CLI ne contiennent aucun login, mot de
passe ou détail cryptographique. Python ne garantit pas la zéroisation d'un
objet `str`; le code limite donc la durée des références sans promettre une
propriété impossible.

## Migration et rollback

`0027` exige une table vide avant de remplacer les contraintes permissives de
G1. Cette dernière fenêtre fermée empêche qu'une ligne d'un format ambigu soit
interprétée comme un credential G5. Le downgrade est également refusé dès
qu'une ligne de cycle de vie existe.

G5A est la cible de rollback immédiate de G5B : la base reste en `0027`. Aucun
downgrade de production n'est prévu après la première enveloppe, même révoquée.

## Restauration

Une restauration de base doit rester arrêtée tant que les sessions PASS/HUB et
les credentials actifs n'ont pas été révoqués par les commandes hors réseau.
Une sauvegarde peut conserver une ancienne enveloppe jusqu'à expiration de sa
rétention ; restaurer la base ne doit jamais la rendre utilisable
silencieusement.

Ordre fermé :

1. exécuter la restauration sur une base isolée ;
2. révoquer les sessions PASS/HUB ;
3. exécuter la révocation globale des credentials avec la raison
   `database_restored` ;
4. vérifier les agrégats et l'absence d'enveloppe active ;
5. seulement ensuite autoriser un démarrage normal.
