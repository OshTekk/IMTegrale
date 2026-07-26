# Migration HPKE des sessions PASS/HUB

## Invariants

La migration `0026` est additive. Elle ajoute l'enveloppe HPKE, sa version, son
`key_id`, la date de migration et l'index de rotation, tout en conservant
temporairement `encrypted_cookie_jar`.

Une ligne ne peut jamais contenir les deux ciphertexts. Une session active
possède exactement une représentation ; une session inactive n'en conserve
aucune. L'enveloppe session fait exactement 65 652 octets. La migration Alembic
ne lit aucune clé et ne modifie aucune ligne.

## G4A : expansion

1. Vérifier l'artefact CI round-trip et la sauvegarde restaurée.
2. Arrêter le scheduler, attendre les leases sync, puis arrêter le worker sync.
3. Appliquer Alembic `0026`.
4. Déployer G4A avec :
   - la seule clé publique session dans le web ;
   - les quatre credentials HPKE dans le worker ;
   - le profil `BOTNOTE_SYNC_RUNTIME_PROFILE=migration` uniquement pour la
     commande de migration ;
   - la clé symétrique historique disponible temporairement au worker G4A.
5. Exécuter hors réseau :

```bash
BOTNOTE_SYNC_RUNTIME_PROFILE=migration botnote pass-sessions-migrate-hpke --dry-run
BOTNOTE_SYNC_RUNTIME_PROFILE=migration botnote pass-sessions-migrate-hpke
BOTNOTE_SYNC_RUNTIME_PROFILE=migration botnote pass-sessions-migrate-hpke --verify-only
```

Les sorties contiennent uniquement des agrégats. `failed`,
`remaining_legacy`, mixed et métadonnées invalides doivent valoir zéro. La
commande verrouille chaque ligne, vérifie le snapshot, scelle puis ouvre
l'enveloppe avant de remplacer le legacy dans le même commit. Une ligne en
échec est rollbackée et conserve son legacy.

6. Repasser le worker G4A au profil normal, démarrer sans scheduler, vérifier le
   self-test et le heartbeat, puis rouvrir le scheduler dans une fenêtre sans
   synchronisation due.

G4A est le rollback immédiat de G4B et comprend les deux formats. Aucun
downgrade de `0026` n'est autorisé après la première enveloppe.

## G4B : contraction

G4B a été appliqué après un nouvel inventaire à zéro. Le runtime normal est
HPKE-only, refuse tout legacy et ne charge plus
`BOTNOTE_CREDENTIAL_KEY` ni `BOTNOTE_CREDENTIAL_PREVIOUS_KEYS`. Le profil
explicite de migration peut rester disponible comme outil d'incident isolé,
mais il n'est importé par aucun worker normal.

Le heartbeat final est limité à :

```text
runtime_profile=isolated-sync-v2
hpke_credentials_ready=true
pass_session_storage=hpke-v1
legacy_decrypt_available=false
dedicated_identity=true
```

Un legacy réintroduit rend `operations-check` non vert et n'est jamais converti
silencieusement.

## Perte de clé

Une clé privée absente met la synchronisation en pause et préserve l'enveloppe.
Après décision humaine, la révocation globale est possible sans réseau :

```bash
botnote pass-sessions-revoke-all \
  --reason key_lost \
  --confirm REVOKE-ALL-PASS-SESSIONS
```

Cette action efface les deux formats, révoque les sessions actives et impose
une reconnexion. Elle ne supprime ni compte, ni note, ni passkey.

## Restauration

Une sauvegarde peut ressusciter une session révoquée après sa création. Toute
base restaurée doit donc être traitée avant remise en service :

```bash
botnote pass-sessions-revoke-all \
  --reason database_restored \
  --dry-run
botnote pass-sessions-revoke-all \
  --reason database_restored \
  --confirm REVOKE-ALL-PASS-SESSIONS
botnote sync-credentials-revoke-all \
  --reason database_restored \
  --dry-run
botnote sync-credentials-revoke-all \
  --reason database_restored \
  --confirm REVOKE-ALL-SYNC-CREDENTIALS
```

La procédure s'exécute dans la base isolée, sans PASS, HUB, COMPETENCES ou
Telegram. Les sessions sont révoquées avant les credentials. Le test mensuel
de restauration doit vérifier l'inventaire ciphertext à zéro pour les deux
tables avant de considérer la restauration exploitable.

## Rollback

Le rollback de G4B rebascule release et runtime vers G4A, restaure son fichier
sync temporaire, puis redémarre worker et scheduler. La base reste en `0026`.
Il ne faut ni downgrader, ni recréer de legacy. Un retour jusqu'à G3 exige au
préalable la révocation de toutes les enveloppes et l'acceptation d'une
reconnexion pour chaque compte concerné.
