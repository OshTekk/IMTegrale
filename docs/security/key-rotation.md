# Rotation des clés et peppers

## Clé de chiffrement

`BOTNOTE_CREDENTIAL_KEY` est l'unique clé d'écriture. `BOTNOTE_CREDENTIAL_PREVIOUS_KEYS` contient temporairement les anciennes clés de lecture, sous forme de liste JSON ou séparée par des virgules. Chaque clé décode exactement 32 octets et possède un `key_id` dérivé, présent dans l'enveloppe AES-GCM `v1`.

Procédure :

1. sauvegarder et tester la restauration avant la rotation ;
2. générer une nouvelle clé avec `python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'` sur un terminal privé ;
3. déplacer la clé active actuelle dans `BOTNOTE_CREDENTIAL_PREVIOUS_KEYS`, placer la nouvelle dans `BOTNOTE_CREDENTIAL_KEY`, puis redémarrer API et workers ;
4. exécuter `botnote keys-reencrypt --dry-run` et conserver uniquement les compteurs et le `key_id` actif ;
5. exécuter `botnote keys-reencrypt --batch-size 100` ; la commande commit par lots, vérifie chaque nouvelle enveloppe et reprend sans effet secondaire après interruption ;
6. exécuter une seconde fois la commande : `reencrypted`, `remaining` et `calendar_digests_remaining` doivent valoir zéro, avec `complete: true` ;
7. après sauvegarde post-rotation et validation applicative, retirer les anciennes clés puis redémarrer les services.

La commande couvre les tokens et Chat IDs Telegram, les URL INPASS et les cookies PASS/HUB. Le contexte AES reste lié au type de secret et à l'identifiant du compte ou de la session. Aucun plaintext n'est écrit dans la sortie.

En cas d'échec, conserver la nouvelle clé active et toutes les anciennes clés de lecture, corriger la cause, puis relancer. Ne jamais retirer une ancienne clé pour forcer la fin d'une rotation.

## Pepper HMAC

`BOTNOTE_TOKEN_PEPPER` signe toute nouvelle empreinte. `BOTNOTE_TOKEN_PREVIOUS_PEPPERS` permet une coexistence explicite pendant la rotation.

- les sessions web et admin sont reconnues avec le pepper actif ou précédent puis réécrites avec l'actif lors de leur utilisation ;
- les tokens partagés sont réécrits avec l'actif lors d'une connexion réussie ;
- la détection de doublon INPASS accepte les empreintes active et précédentes ; `botnote keys-reencrypt` recalcule aussi les empreintes calendrier déchiffrables avec le pepper actif ;
- les nouvelles sessions, nouveaux tokens et nouvelles URL utilisent uniquement le pepper actif.

Une empreinte HMAC ne peut pas être migrée sans revoir sa valeur brute. Avant de retirer un ancien pepper :

1. noter l'heure de bascule ;
2. attendre l'expiration maximale des sessions web et admin, puis purger les sessions expirées ;
3. exécuter et vérifier `botnote keys-reencrypt` pour les calendriers ;
4. attendre l'expiration des tokens bornés créés avant la bascule ;
5. révoquer et réémettre tous les tokens sans expiration créés avant la bascule, car un token dormant ne peut pas être distingué ou réécrit sans être présenté ;
6. retirer l'ancien pepper, redémarrer, puis vérifier connexions, révocations et readiness.

Retirer prématurément un ancien pepper est une révocation explicite, jamais une migration silencieuse. Le rollback consiste à remettre le pepper retiré dans la liste de lecture ; il ne doit être fait que depuis la source de secrets privée.
