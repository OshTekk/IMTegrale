# Profil ZIP strict des wheels de release

## Portée

Ce document décrit la frontière structurelle M4A associée au finding
`csf_98540b995f92f957d522e1ea` (`C7-D-004`, medium/P2). Le contrôle s'applique
aux wheels produites par le build de release verrouillé d'IMTégrale. Il ne vise
pas la compatibilité avec toutes les archives ZIP valides en théorie.

M4A ne valide pas encore la sémantique complète de `METADATA`, `WHEEL`,
`RECORD`, `entry_points.txt`, `top_level.txt` ou des licences. Cette couche est
réservée à M4B. La liaison entre les octets scannés et l'objet ensuite publié
est réservée à M5.

## Profil observé

La wheel construite depuis la base M4A portait le nom logique
`botnote_imt-4.8.0-py3-none-any.whl`. Un build de référence mesurait 249 599
octets, 89 membres et le SHA-256
`93e9a96056fd813340b8bc1b7bc13f27cdda8da280287088ac7e49aaf8a25fd5`.
Le manifeste de release fixe le digest de chaque exécution ; ce document ne
transforme pas ce digest de mesure en snapshot de release.

Le profil binaire observé est le suivant :

| Propriété | Valeur observée |
| --- | ---: |
| Méthode de compression | DEFLATE (`8`) uniquement |
| Flags centraux et locaux | `0x0000` |
| Data descriptors | 0 |
| Indicateurs ZIP64 | 0 |
| Commentaire d'archive | 0 octet |
| Commentaires de membre | 0 |
| Extras centraux | 0 |
| Extras locaux | 0 |
| Entrées répertoire | 0 |
| Taille du central directory | 6 422 octets |
| Offset du central directory | 243 155 |
| Offset EOCD | 249 577 |
| Préfixe / octets terminaux | 0 / 0 |
| Gaps / chevauchements | 0 / 0 |

Le build est donc compatible avec un profil fail-closed sans ZIP64, commentaire,
extra field, entrée répertoire, chiffrement, multidisque ni data descriptor.

## Décision structurelle

`scan_wheel()` ouvre la cible en lecture seule, utilise `O_NOFOLLOW` lorsqu'il
est disponible, vérifie le type avec `fstat`, puis transmet le même descripteur
au parseur spécialisé. Le parseur n'extrait rien, n'importe pas le package, ne
suit aucun lien et ne contacte aucun réseau.

La décision suit cet ordre :

1. recherche EOCD bornée depuis la fin et sélection d'une unique candidate
   cohérente ;
2. parcours séquentiel exact du central directory ;
3. résolution de chaque offset vers un unique local header ;
4. comparaison des noms bruts, versions, flags, méthodes, dates, CRC et tailles ;
5. inventaire de toutes les régions locales, centrales et terminales ;
6. rejet de tout gap, chevauchement, préfixe, trailing byte ou header orphelin ;
7. décompression DEFLATE bornée, puis vérification de la taille et du CRC ;
8. application des règles génériques de chemin, magic et sentinelles aux noms,
   métadonnées et payloads accessibles en sécurité.

Les informations internes sont immuables et limitées aux offsets, tailles,
flags, méthode, CRC, noms bruts/décodés et classes de régions nécessaires à la
décision. Les diagnostics publics ne conservent ni chemin, ni valeur de
métadonnée, ni contenu.

## Couverture des octets

Chaque membre contribue les régions suivantes : header local fixe, nom local,
extra local, payload compressé, header central fixe, nom central, extra central
et commentaire membre. L'EOCD fixe et son commentaire complètent l'inventaire.

Un succès exige simultanément :

```text
regions_unclassified = 0
overlaps = 0
gaps = 0
bytes_classified = bytes_total
```

La wheel de référence produit 714 régions internes, 249 599 octets classifiés
sur 249 599, zéro gap, zéro chevauchement et 89 payloads vérifiés.

## Politique de refus

Le profil refuse notamment : commentaires, extras locaux ou centraux, entrées
répertoire, liens et fichiers spéciaux, chiffrement, flags supplémentaires,
méthodes autres que DEFLATE, multidisque, ZIP64, data descriptors, noms ambigus,
doublons et collisions normalisées. Il refuse aussi toute incohérence EOCD,
centrale/locale, CRC, taille ou offset, ainsi que toute région inconnue.

Les rule IDs stables couvrent les familles suivantes :

- `WHEEL_EOCD_INVALID`, `WHEEL_MULTIDISK_FORBIDDEN`,
  `WHEEL_ZIP64_FORBIDDEN`, `WHEEL_TRAILING_BYTES_FORBIDDEN` ;
- `WHEEL_CENTRAL_DIRECTORY_INVALID`, `WHEEL_LOCAL_HEADER_INVALID`,
  `WHEEL_CENTRAL_LOCAL_MISMATCH`, `WHEEL_LOCAL_OFFSET_DUPLICATE` ;
- `WHEEL_ARCHIVE_COMMENT_FORBIDDEN`, `WHEEL_MEMBER_COMMENT_FORBIDDEN`,
  `WHEEL_CENTRAL_EXTRA_FORBIDDEN`, `WHEEL_LOCAL_EXTRA_FORBIDDEN` ;
- `WHEEL_DIRECTORY_ENTRY_FORBIDDEN`, `WHEEL_SYMLINK_ENTRY`,
  `WHEEL_SPECIAL_FILE_FORBIDDEN`, `WHEEL_ENCRYPTION_FORBIDDEN`,
  `WHEEL_DATA_DESCRIPTOR_FORBIDDEN`, `WHEEL_COMPRESSION_METHOD_INVALID` ;
- `WHEEL_REGION_GAP`, `WHEEL_REGION_OVERLAP`, `WHEEL_PREFIX_FORBIDDEN`,
  `WHEEL_BYTES_UNCLASSIFIED`, `WHEEL_LOCAL_HEADER_ORPHAN` ;
- `WHEEL_DECOMPRESSION_INVALID`, `WHEEL_CRC_MISMATCH`,
  `WHEEL_SIZE_MISMATCH` et les limites de fichier, membre, total, ratio et
  nombre d'entrées.

## Limites et performance

Le fichier compressé et le total décompressé sont limités à 256 MiB, chaque
membre à 64 MiB, le ratio déclaré à 200 et le nombre de membres à 20 000. La
reconnaissance LZMA générique utilisée par les règles de magic plafonne aussi
son dictionnaire de détection à 1 MiB ; la sortie inspectée reste limitée à un
octet.

Mesures locales Python 3.12, incluant `scan_wheel()` complet :

| Cas | Durée | Pic mémoire Python | Membres | Régions | Octets |
| --- | ---: | ---: | ---: | ---: | ---: |
| Wheel réelle | 53,64 ms | 1,33 MiB | 89 | 714 | 249 599 |
| Proche limite | 9,72 s | 51,95 MiB | 19 999 | 159 994 | 2 099 917 |
| Payload compressé raisonnable | 7,98 ms | 2,06 MiB | 1 | 10 | 1 049 024 |

Le cas limite est volontairement beaucoup plus grand que la wheel réelle et
reste borné pour une CI. La couverture n'est pas réduite pour optimiser ce cas.

## Chaîne CI

La CI scanne la wheel construite avant l'upload. Le job round-trip télécharge
ensuite l'artefact exact, vérifie son manifeste, rescane la wheel téléchargée
avec le scanner du head et rejoue le smoke-test. Il ne reconstruit ni ne répare
une seconde wheel. Ce contrôle ne déploie rien et ne remplace pas la future
liaison M5 entre scan et publication.
