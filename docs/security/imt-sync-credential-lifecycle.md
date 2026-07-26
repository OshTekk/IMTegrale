# Cycle de vie du credential IMT

## État G5A

La migration additive `0027` prépare le cycle de vie sans rendre le stockage
utilisable. Elle ne crée aucune ligne, ne charge aucune clé et n'effectue aucun
appel réseau. La production attend donc toujours :

```text
active = 0
invalid = 0
revoked = 0
autonomous = 0
```

L'API d'enrôlement, la révocation et la purge appartiennent à G5B. Le worker
sync n'interroge pas cette table et son rôle PostgreSQL reste explicitement
refusé.

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

## Migration et rollback

`0027` exige une table vide avant de remplacer les contraintes permissives de
G1. Cette dernière fenêtre fermée empêche qu'une ligne d'un format ambigu soit
interprétée comme un credential G5. Le downgrade est également refusé dès
qu'une ligne de cycle de vie existe.

G5A est la cible de rollback immédiate de G5B : la base reste en `0027`. Aucun
downgrade de production n'est prévu après la première enveloppe, même révoquée.

## Restauration

Une restauration de base doit rester arrêtée tant que les sessions PASS/HUB et
les futurs credentials actifs n'ont pas été révoqués par les commandes hors
réseau prévues. Une sauvegarde peut conserver une ancienne enveloppe jusqu'à
expiration de sa rétention ; restaurer la base ne doit jamais la rendre
utilisable silencieusement.
