# Format des enveloppes HPKE

## Portée

Le gate G2 fournit une primitive interne générique. Elle n'est reliée ni aux
comptes, ni à PostgreSQL, ni aux API, ni aux sessions réelles, ni aux workers.
Elle ne lit aucune configuration et ne charge aucun fichier de clé.

Le module utilise directement
[l'API one-shot HPKE de `cryptography 49`](https://cryptography.io/en/49.0.0/hazmat/primitives/hpke/)
conforme au format défini par la
[RFC 9180](https://www.rfc-editor.org/rfc/rfc9180.html) :

- mode HPKE : base ;
- KEM : X25519 ;
- KDF : HKDF-SHA-256 ;
- AEAD : ChaCha20-Poly1305 ;
- identifiant de suite IMTégrale : `1`.

Le contexte applicatif canonique est fourni exclusivement par le paramètre
`info` de `Suite.encrypt()` et `Suite.decrypt()`. Aucun second AAD, nonce,
échange X25519, HKDF ou assemblage `enc || ct` n'est implémenté par
IMTégrale.

## Versions et identifiants

| Élément | Version ou identifiant |
| --- | --- |
| Enveloppe binaire | `1` |
| Schéma `info` | `1` |
| Suite IMTégrale | `1` |
| Purpose credential IMT | `1` (`imt-sync-credential`) |
| Purpose session PASS/HUB | `2` (`pass-service-session`) |
| Profil mot de passe | `1` (`imt-password-frame-v1`) |
| Profil session technique | `2` (`pass-service-session-v1`) |

Une version, une suite, un purpose ou un profil inconnu est refusé. Il n'existe
aucun fallback permissif.

## Exemple fictif

Le code suivant illustre uniquement le contrat interne. `test_private_key` est
une clé X25519 éphémère créée par un test ; aucune clé n'est lue depuis un
fichier, une variable d'environnement ou la base.

```python
context = ImtSyncCredentialContext(
    account_id="11111111-1111-4111-8111-111111111111",
    imt_login="student.fixture",
    credential_generation=1,
    consent_version=1,
)
frame = encode_imt_password_frame("synthetic-test-value")
envelope = seal_envelope(
    test_private_key.public_key,
    purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
    profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
    context=context,
    plaintext=frame,
)
```

G2 n'expose cet appel depuis aucun routeur, worker ou modèle persistant. Le
smoke-test du wheel réalise le même type de round-trip uniquement en mémoire.

## Enveloppe binaire v1

Les entiers multi-octets sont en ordre réseau. Le header fait exactement
52 octets.

| Offset | Taille | Champ |
| ---: | ---: | --- |
| 0 | 8 | Magic `IMTHPKE\0` |
| 8 | 1 | Version d'enveloppe |
| 9 | 1 | Version du schéma `info` |
| 10 | 1 | `suite_id` |
| 11 | 1 | `purpose_id` |
| 12 | 1 | `plaintext_profile_id` |
| 13 | 32 | SHA-256 brut de la clé publique destinataire |
| 45 | 4 | Longueur du payload HPKE |
| 49 | 3 | Réservé, obligatoirement nul |
| 52 | variable bornée | Valeur `enc || ct` renvoyée par `Suite.encrypt()` |

Le parser applique la borne globale avant toute copie. Il refuse les données
tronquées, les octets supplémentaires, une longueur incohérente et tout octet
réservé non nul.

Pour le profil credential :

- frame plaintext : 3 072 octets ;
- encapsulation X25519 : 32 octets ;
- tag ChaCha20-Poly1305 : 16 octets ;
- payload HPKE : 3 120 octets ;
- enveloppe complète : **3 172 octets**, donc sous la borne SQL de 4 096.

Le profil session accepte de 1 à 32 768 octets de plaintext. La borne globale
d'une enveloppe v1 est 32 868 octets.

## `key_id`

Le `key_id` est le SHA-256 complet des 32 octets raw canoniques de la clé
publique X25519 :

- 32 octets dans l'enveloppe ;
- 64 caractères hexadécimaux minuscules à l'extérieur ;
- aucune troncature ;
- aucune donnée issue de la clé privée ou du secret.

Le keyring privé indexe exactement les clés par cet identifiant. Il refuse les
doublons et les index ne correspondant pas à la clé. À l'ouverture, il
sélectionne uniquement l'identifiant déclaré et n'essaie jamais toutes les clés.
Une clé active d'écriture reste distincte des anciennes clés de lecture.

## Encodage canonique de `info`

L'encodage est binaire, ordonné et sans dictionnaire libre :

1. longueur `u8` du domaine puis `IMTegrale/internal-hpke` ;
2. version `info`, version enveloppe, suite, purpose, profil et longueur du
   `key_id`, chacun sur un octet ;
3. digest complet du `key_id` ;
4. longueur du payload HPKE sur `u32` ;
5. type de contexte et nombre de champs sur un octet chacun ;
6. chaque champ sous la forme `tag u8 || longueur u16 || valeur`.

Les UUID sont les chaînes canoniques de 36 caractères utilisées par le projet.
Les entiers de génération et de consentement sont des `u64` strictement
positifs. Le login suit la règle canonique du projet : espaces externes retirés,
puis minuscules ; les espaces internes et caractères de contrôle sont refusés.

Deux contextes immuables existent :

- credential : compte, login IMT, génération du credential, version de
  consentement ;
- session : compte, login IMT, identifiant de session technique.

Le header sémantique et tous ces champs sont liés cryptographiquement par
`info`. Une modification du compte, du login, de la génération, du consentement,
du purpose, du profil, de la suite, de la version, de la longueur ou du
`key_id` empêche l'ouverture.

## Frame credential v1

Le codec préserve exactement le mot de passe et ne fait ni `strip`, ni
normalisation Unicode, ni changement de casse.

| Offset | Taille | Champ |
| ---: | ---: | --- |
| 0 | 8 | Magic `IMTPWD\0\0` |
| 8 | 1 | Version `1` |
| 9 | 1 | Réservé, obligatoirement nul |
| 10 | 2 | Longueur UTF-8 réelle sur `u16` |
| 12 | 1 à 2 048 | Octets UTF-8 exacts |
| suivant | jusqu'à 3 072 | Padding aléatoire non interprété |

Le secret comprend 1 à 512 caractères et au plus 2 048 octets UTF-8. Le frame
fait toujours 3 072 octets : deux secrets de longueurs différentes produisent
des enveloppes de même taille. Python ne garantit cependant pas une zéroisation
parfaite de toutes les copies mémoire.

## Erreurs

Le module expose des erreurs stables pour :

- clé invalide ;
- contexte invalide ;
- format invalide ou version non supportée ;
- clé privée absente ;
- authentification cryptographique échouée ;
- chiffrement échoué ;
- frame secret invalide.

`InvalidTag` et les erreurs cryptographiques internes ne traversent pas la
frontière du module. Les messages, représentations et logs ne contiennent ni
contexte, ni clé raw, ni plaintext, ni ciphertext.

## Garanties et limites

HPKE base mode protège la confidentialité et l'intégrité du plaintext pour le
détenteur de la clé privée. Toute entité détenant la clé publique peut toutefois
produire une enveloppe : le mode base n'authentifie pas son producteur.

Le format ne fournit pas :

- l'autorisation d'enrôlement ;
- la preuve ou le retrait du consentement ;
- l'anti-replay ;
- la révocation ;
- la validation d'une génération courante ;
- la forward secrecy après fuite de la clé privée ;
- la protection d'un secret avant chiffrement dans un processus web compromis.

Ces contrôles appartiendront aux gates ultérieurs et à la base de données. Une
RCE du futur worker détenteur de la clé privée restera critique.

## Compatibilité future

Les nouvelles suites, structures de contexte ou frames nécessiteront de nouveaux
identifiants et tests de migration. Une ancienne implémentation refuse une
version inconnue au lieu de tenter une interprétation. Les anciennes clés
privées pourront rester temporairement dans le keyring de lecture, sélectionnées
par leur `key_id`, sans devenir des clés actives d'écriture.

Les exemples et tests emploient uniquement des identités, clés et secrets
fictifs générés en mémoire. Aucune valeur opérationnelle n'est incluse dans le
wheel ou l'artefact de release.
