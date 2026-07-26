# Rotation opérationnelle HPKE

## Invariants

Les purposes `imt-sync-credential` et `pass-service-session` possèdent chacun
une génération active. Le manifest keyset v2 référence des fichiers versionnés
root-only ; il ne contient aucune clé. Les chemins sont fixes, sans symlink, et
les fichiers doivent être réguliers, `0400`, avec un seul lien physique.

Une génération est préparée, puis les enveloppes sont tournées hors réseau,
avant l'activation et le retrait de l'ancienne clé. L'ancienne clé privée n'est
jamais injectée dans un service normal après le cutover.

## Gestion du keyset

Le provisionneur accepte encore un keyset v1 et l'étend en v2 lors de la
première préparation. Une installation neuve suit ce même passage explicite :
`provision` crée v1, puis les deux générations v2 sont préparées et activées
avant l'installation des unités G6B. Ce bootstrap se fait avec un inventaire
d'enveloppes nul et sans démarrer les services entre les deux générations.

Commandes de préparation :

```bash
deploy/security/provision-sync-hpke-keys verify
deploy/security/provision-sync-hpke-keys prepare-rotation \
  --purpose pass-service-session --generation 2
deploy/security/provision-sync-hpke-keys activate-generation \
  --purpose pass-service-session --generation 2 \
  --confirm ACTIVATE-HPKE-GENERATION
```

`prepare-rotation` n'écrase aucun fichier et ne change pas la génération active.
`activate-generation` ne migre aucune ligne. `retire-generation` refuse un
inventaire non nul et ne supprime une clé privée qu'avec confirmation explicite.

## Rotation des enveloppes

`botnote hpke-rotate-envelopes` exige le profil
`BOTNOTE_SYNC_RUNTIME_PROFILE=migration` et le rôle applicatif
`hpke-rotation`. Ce rôle refuse de démarrer si la clé symétrique applicative,
ses anciennes clés, le token pepper ou ses anciennes valeurs sont présents.
Il exige PostgreSQL local par authentification peer. L'unité opérationnelle
temporaire n'importe donc aucun fichier d'environnement web ou worker et lui
fournit exactement :

- l'ancienne clé privée ;
- la nouvelle clé privée ;
- la nouvelle clé publique.

Elle ne reçoit ni mot de passe utilisateur, ni secret Telegram, ni clé web. Son
espace réseau doit être fermé ; PostgreSQL reste accessible par socket Unix.
Elle s'exécute comme `botnote-sync`, avec `PrivateNetwork=yes`,
`NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes` et
`LimitCORE=0`. Les trois clés sont injectées par `LoadCredential=` sous les
noms fixes documentés par la commande, puis l'unité transitoire est collectée.

Pour chaque ligne source, la commande :

1. verrouille la ligne ;
2. ouvre avec l'ancien contexte exact ;
3. scelle avec la nouvelle clé publique ;
4. réouvre avec la nouvelle clé privée ;
5. compare le plaintext ;
6. remplace atomiquement enveloppe, version et `key_id`.

Les dates métier, l'état, la génération et le consentement sont préservés.
`--dry-run` effectue le round-trip sans écriture. `--verify-only` ouvre
cryptographiquement toutes les lignes cibles. La sortie contient uniquement les
compteurs `source_found`, `rotated`, `already_target`, `inactive_ignored`,
`mixed_active`, `invalid_metadata`, `failed` et `remaining_source`.

## Ordre de production

1. sauvegarder PostgreSQL chiffré et restaurer le dump dans une base isolée ;
2. vérifier zéro job, lease ou opération PASS en cours ;
3. préparer les générations v2 des deux purposes ;
4. vérifier zéro credential IMT, tourner puis activer ce purpose ;
5. arrêter scheduler puis worker sync ;
6. faire le dry-run des sessions v1 vers v2 ;
7. exiger `failed=0`, `invalid_metadata=0` et `mixed_active=0` ;
8. exécuter la rotation confirmée ;
9. exiger `remaining_source=0`, puis lancer `--verify-only` ;
10. basculer les `LoadCredential` web et worker vers les fichiers v2 ;
11. déployer et vérifier web, worker, heartbeats et readiness ;
12. redémarrer le scheduler dans une fenêtre sans travail dû ;
13. tester le rollback applicatif G6A avec les mappings v2 ;
14. seulement après inventaire nul, retirer l'ancienne clé privée.

La commande ne reconstruit aucune session, ne contacte aucun service, ne
modifie aucune note, aucun mode ni aucun consentement.

## Échec et reprise

La rotation est idempotente. Une ligne déjà cible est vérifiée ou comptée sans
être réécrite. Une ligne active utilisant une troisième clé est comptée dans
`mixed_active` et bloque la commande. Une ligne invalide fait échouer le lot
sans afficher son identité. Les mutations du manifeste sont sérialisées sur le
répertoire du keyset et restaurent le manifeste précédent si la validation
post-écriture échoue. Ne jamais retirer l'ancienne clé pour forcer la fin d'une
migration.

Le rollback G6B utilise la release G6A, conserve Alembic `0028` et les mappings
v2, sans downgrade ni retour des enveloppes vers v1. Après restauration d'une
base, révoquer toutes les sessions et tous les credentials avant remise en
réseau.
