# Chaîne de release immuable

## Contrat C6C

Une release IMTégrale possède un seul payload canonique : **IMTégrale Release
Capsule v1**. Le builder copie les entrées depuis des descripteurs stables,
construit un ZIP déterministe, calcule son SHA-256 puis publie uniquement le
fichier `imtegrale-release-<sha256>.zip`. Le répertoire de build n'est plus une
source de publication après cette étape.

Tous les contrôles release exigent simultanément :

```text
--snapshot <imtegrale-release-<sha256>.zip>
--expected-sha256 <sha256>
```

Ils refusent symlink, hardlink, fichier spécial, remplacement de chemin,
mutation pendant lecture, nom non content-addressed et digest divergent. Ils
ouvrent avec `O_NOFOLLOW` lorsque le système le permet, comparent `lstat` et
`fstat`, hashent le descripteur, puis inspectent ou extraient depuis ce même
descripteur. Une extraction est privée, créée avec `O_EXCL`, et chaque fichier
est revérifié contre le manifeste avant consommation.

## Inventaire des formats publiés

L'inventaire C6C de la branche avant correction a observé dix PNG suivis par
Git, 116 fichiers frontend dont 59 fontes KaTeX, un manifest Vite caché, un
wheel Python, un SBOM CycloneDX et un manifeste de release. Aucun fichier suivi
ni membre individuel de l'artefact C6B ne dépassait cinq Mio. Le wrapper GitHub
C6B contenait 119 fichiers et un wheel imbriqué.

| Format | Chemins | Contrôle C6C | Contenu | Métadonnées | Rejet |
| --- | --- | --- | --- | --- | --- |
| Texte UTF-8 | dépôt, frontend, wheel, SBOM, manifests | streaming sans seuil silencieux | toutes les règles bytes et URL INPASS | chemin et stabilité du descripteur | encodage, NUL, mutation ou lecture impossible |
| ZIP / wheel | snapshots, wrappers GitHub, wheels | parser structurel borné + `zipfile` pour décompression | octets compressés et contenu décompressé | EOCD/ZIP64, central/local, descriptors, noms bruts/décodés, extras, commentaires, attributs | toute ambiguïté, région terminale, chiffrement, méthode inconnue ou limite dépassée |
| PNG de référence | dix snapshots visuels suivis | SHA-256, taille et chemin exacts | digest couvrant tous les octets | entrée versionnée | tout octet, chemin ou taille divergent |
| TTF/WOFF/WOFF2 KaTeX | `frontend/assets/*` issu du manifest Vite | SHA-256, taille, type, provenance et chemins exacts | digest couvrant tous les octets | provenance KaTeX 0.18.1 exacte | format opaque sans entrée, trailing ou polyglotte modifiant le digest |
| Autre binaire | tout emplacement | aucune autorisation par extension, MIME ou magic | aucune | aucune | rejet fermé |
| Autre archive | tout emplacement | non inventoriée | aucune | aucune | rejet fermé |

La politique binaire est dans
[`scripts/security_binary_allowlist.json`](../../scripts/security_binary_allowlist.json).
Chaque entrée possède un digest, une taille, un type logique, un media type, un
purpose fermé, une provenance, une politique et des chemins exacts. Les globs,
racines larges, champs supplémentaires, doublons, ordre non déterministe et
entrées actives inutilisées sont refusés. Un magic sert uniquement à classer un
objet comme binaire ; il ne l'autorise jamais.

## Scanner ZIP brut et sémantique

[`archive_scanner.py`](../../scripts/security_scan/archive_scanner.py) lit les
régions basses du ZIP indépendamment de l'abstraction de haut niveau :

- EOCD et EOCD/locator ZIP64 ;
- chaque central directory entry et local file header ;
- data descriptor signé ou non, 32 ou 64 bits ;
- noms bruts local/central et noms décodés ;
- extra fields bruts, TLV, ZIP64 et Unicode path ;
- commentaires globaux et membres, bruts et décodés lorsque possible ;
- attributs externes, flags, méthode, CRC, tailles et offsets ;
- flux compressé brut et contenu décompressé ;
- archives imbriquées jusqu'à la profondeur autorisée.

Il exige la cohérence local/central, l'absence de gap, préfixe polyglotte et
trailing bytes, et refuse chemins absolus, drives Windows, `..`, backslashes,
NUL, formes Unicode non canoniques, doublons, collisions Unicode/casefold,
collisions fichier/répertoire, symlinks, fichiers spéciaux et exécutables.

Limites v1 : 20 000 membres, 512 Mio par membre, 2 Gio décompressés au total,
ratio 500 après un Mio, profondeur trois, huit Mio de métadonnées, nom 4 096
octets, commentaire/extra field 65 535 octets et budget de traitement de 120
secondes. Le chiffrement, le multi-disque et les méthodes autres que stored ou
deflate sont refusés. Une limite est toujours un échec, jamais un skip.

Le rapport homogène contient notamment `archives_scanned`,
`archive_members_scanned`, `archive_metadata_regions_scanned`,
`archive_comments_scanned`, `archive_extra_fields_scanned`,
`archive_directory_entries_scanned`, `nested_archives_scanned`,
`compressed_bytes_scanned`, `decompressed_bytes_scanned`,
`archive_regions_rejected` et `archive_regions_unscanned`. Un succès exige
`archive_regions_unscanned=0` et `files_unscanned=0`.

## Exemptions exactes de secrets

[`scripts/security_secret_exemptions.json`](../../scripts/security_secret_exemptions.json)
ne contient aucune valeur brute. Une exemption lie :

- `rule_id` exact ;
- chemin de fixture exact sous `backend/tests/` ;
- SHA-256 des bytes exacts de la correspondance complète ;
- purpose fermé `synthetic_detector_fixture` ;
- nombre maximal d'occurrences faible.

La comparaison du digest est en temps constant. Le texte voisin, le numéro de
ligne, les mots « synthetic » ou « fixture », le parent du chemin et
l'extension n'autorisent rien. Une autre valeur sur la même ligne, la même
valeur dans un autre fichier ou dans une archive, une occurrence excédentaire
et une exemption active inutilisée font échouer le scan. Les diagnostics ne
contiennent que règle, chemin/offset non sensible et `match=[REDACTED]`.

## Construction de la capsule

[`build_release_snapshot.py`](../../scripts/build_release_snapshot.py) applique
`umask 077`, crée un staging privé, refuse liens et modes inattendus, puis pour
chaque source :

1. `lstat`, ouverture `O_NOFOLLOW`, comparaison `fstat` ;
2. copie et SHA-256 depuis le même descripteur vers un fichier `O_EXCL` ;
3. comparaison device, inode, taille, `mtime_ns`, `ctime_ns`, mode et `nlink`
   avant/après ;
4. `fsync` du fichier et des répertoires ;
5. manifeste créé uniquement depuis les copies.

Le ZIP canonique utilise `ZIP_STORED`, ordre lexicographique, date fixe
2020-01-01 UTC, modes réguliers `0444`, aucun UID/GID, commentaire, extra field,
répertoire implicite ou data descriptor. Son manifeste contient version de
schéma, commit source, version du contrat, liste triée des payloads, chemin,
taille, digest, mode, type, rôle, nombre total et taille totale. Le manifeste
est l'unique entrée implicitement auto-inventoriée : son propre contenu et son
rôle `manifest` sont scellés par le SHA-256 externe de la capsule, évitant un
digest auto-référentiel impossible.

À entrées identiques, deux capsules sont byte-for-byte identiques. Une mutation
du build après construction n'affecte pas la capsule. Une mutation de la
capsule invalide le digest attendu avant extraction.

## CI, upload et round-trip

Le job `release-artifact` :

1. construit wheel, frontend et SBOM ;
2. construit une fois la capsule et exporte `snapshot_sha256` ;
3. exécute scanner, content boundary, audit, verifier, smoke-test et préparation
   d'upload sur la capsule avec ce digest ;
4. fournit à `upload-artifact` le chemin exact du seul fichier capsule.

Le job `release-artifact-roundtrip` télécharge le wrapper GitHub, exige un seul
fichier portant le nom content-addressed, compare son SHA-256 au digest
pré-upload, puis rejoue les mêmes cinq contrôles. Il ne rebuild, ne complète et
ne restaure aucun fichier. Le garde
[`validate_release_workflow.py`](../../scripts/validate_release_workflow.py)
interdit statiquement les chemins `build-inputs`/`frontend/dist`, les modes
répertoire, tout rebuild après snapshot, un upload de répertoire, l'absence du
SHA attendu et tout fallback.

Les anciennes fonctions répertoire ne servent qu'aux fixtures de test et
exigent l'option explicite `--non-release-directory`. Aucun workflow de
publication ne l'utilise.

## Mutation testing

Le harness
[`test_supply_chain_mutations_c6c.py`](../../backend/tests/test_supply_chain_mutations_c6c.py)
copie uniquement l'arbre sécurité dans un répertoire temporaire distinct,
applique un mutant, lance le test de sécurité associé et exige un échec pytest
causé par l'invariant. `tmp_path` détruit ensuite la copie ; le worktree n'est
jamais muté. Les dix mutants C6C sont tous tués :

| Mutant | Invariant qui le tue |
| --- | --- |
| commentaires ZIP non scannés | commentaire global et commentaire membre doivent déclencher la règle redacted |
| extra fields non scannés | chaque carrier de métadonnée ZIP est observé |
| magic binaire autorisant seul | un WOFF non listé reste refusé |
| allowlist binaire par suffixe | digest exact au mauvais chemin reste refusé et inutilisé |
| exemption Telegram contextuelle | valeur voisine sur la même ligne reste détectée |
| exemption active inutilisée acceptée | toute exemption inutilisée rend le rapport non conforme |
| verifier autorisé à rouvrir le build | garde statique du workflow immutable-only |
| upload direct de `frontend/dist` | upload limité au chemin fichier du snapshot |
| comparaison du SHA attendu supprimée | digest attendu obligatoire pour chaque consommateur |
| fallback vers un répertoire | aucun mode directory dans la publication ou le round-trip |

Les cas de données complémentaires couvrent aussi préfixe/trailer binaire,
polyglottes WOFF/ZIP, collisions et trailing ZIP, manifestes trop larges,
symlink/hardlink, remplacement de chemin, incohérences manifeste/archive et
mutation d'un octet du snapshot.

## Déploiement et risque résiduel

Un futur déploiement doit télécharger la capsule et son digest attendu,
vérifier structure/manifeste, extraire dans un nouveau répertoire immuable,
revérifier chaque fichier, installer puis basculer atomiquement. Il ne doit
jamais lire le worktree, reconstruire, régénérer un manifeste ni compléter un
frontend.

C6C n'active aucun feature flag, ne modifie ni n'applique la migration `0029`,
ne déploie rien et ne contacte aucun service institutionnel. La vérification
indépendante C7 reste obligatoire avant toute décision d'activation.
