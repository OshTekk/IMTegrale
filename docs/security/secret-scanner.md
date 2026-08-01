# Scanner de secrets et comptabilité de couverture

Le scanner [`scripts/check_secrets.py`](../../scripts/check_secrets.py) couvre
le dépôt, un wheel, un répertoire local explicitement non-release, une capsule
canonique et un wrapper GitHub externe. Le chemin de publication accepte
uniquement la capsule et son SHA-256 attendu.

Les textes sont lus par blocs de 64 Kio avec chevauchement borné ; le test de
50 Mio prouve l'absence de seuil silencieux. Les archives utilisent le parser
ZIP brut et sémantique décrit dans la
[chaîne de release](release-supply-chain.md). Les formats opaques sont liés à
l'allowlist SHA-256 exacte ou rejetés. Les seules exemptions sont des digests de
valeurs de fixture exactes.

Tous les modes exposent les compteurs obligatoires `files_seen`,
`files_scanned`, `bytes_scanned`, `text_files_scanned`, compteurs binaires,
archives/régions, exemptions, rejets et `files_unscanned`. L'absence d'un
compteur est une erreur interne. Un succès exige simultanément :

```text
files_unscanned = 0
archive_regions_unscanned = 0
binary_regions_unscanned = 0
unused_binary_allowlist_entries = 0
unused_secret_exemptions = 0
```

Exemples :

```bash
python scripts/check_secrets.py
python scripts/check_secrets.py --wheel /tmp/package.whl
python scripts/check_secrets.py \
  --snapshot /tmp/imtegrale-release-<sha256>.zip \
  --expected-sha256 <sha256>
python scripts/check_secrets.py \
  --external-artifact /tmp/github-wrapper.zip \
  --expected-sha256 <wrapper-sha256>
```

Les sorties n'affichent jamais la correspondance complète. Ajouter un format
binaire ou une exemption exige une entrée minimale, triée, revue, un test de
mutation et une mise à jour de l'inventaire C6C.

## Budget et relevé de performance C6C

Le relevé du 1er août 2026 a été exécuté sous macOS arm64 et Python 3.12.13
avec `/usr/bin/time -l`. Le débit est calculé sur les octets texte ou
décompressés effectivement comptabilisés ; le RSS est le maximum du processus
scanner, pas une estimation à partir de la taille des entrées.

| Corpus | Octets utiles | Durée | Débit | RSS maximal |
| --- | ---: | ---: | ---: | ---: |
| dépôt, 577 fichiers | 7,79 Mio | 1,67 s | 4,66 Mio/s | 28,31 Mio |
| texte synthétique exact | 50,00 Mio | 8,60 s | 5,81 Mio/s | 25,56 Mio |
| ZIP texte réaliste, 4,12 Mio compressés | 5,42 Mio | 1,77 s | 3,06 Mio/s | 25,88 Mio |
| wheel backend, 97 membres | 1,14 Mio | 0,74 s | 1,54 Mio/s | 26,03 Mio |
| capsule C6C, wheel imbriqué inclus | 6,14 Mio | 2,14 s | 2,87 Mio/s | 26,78 Mio |
| wrapper GitHub C6B téléchargé | 6,13 Mio | 1,59 s | 3,86 Mio/s | 27,16 Mio |

Le test de 50 Mio exige en plus `bytes_scanned=52_428_800` et
`files_unscanned=0`. Le lecteur texte, les données ZIP compressées et les
membres décompressés avancent par blocs de 64 Kio avec un chevauchement regex
borné ; aucune de ces entrées n'est chargée intégralement. Les seuls buffers de
métadonnées sont eux-mêmes bornés par les limites ZIP. Le budget opérationnel
est de 120 secondes par arbre d'archives, avec les plafonds membre/total/ratio
et profondeur décrits dans la chaîne de release. Dépasser l'un de ces budgets
rejette l'entrée et incrémente les compteurs d'échec ; il n'existe aucun skip
de performance silencieux.
