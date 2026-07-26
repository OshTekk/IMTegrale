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

La commande couvre les tokens et Chat IDs Telegram ainsi que les URL INPASS.
Depuis G4, elle ne traite plus les sessions PASS/HUB : celles-ci utilisent une
enveloppe HPKE liée au compte, au login et à l'identifiant de session. Leur
migration legacy possède une commande séparée et le worker normal ne charge
plus la clé symétrique depuis G4B. Aucun plaintext n'est
écrit dans les sorties.

## Clé HPKE des sessions PASS/HUB

Les nouvelles sessions sont scellées avec la clé publique active. Le worker
ouvre uniquement le `key_id` déclaré avec son keyring privé et rechiffre avec la
clé publique active lors d'un refresh. G4 ne crée pas de seconde clé
opérationnelle. Une rotation future doit inventorier les lignes par
`hpke_key_id`, conserver les anciennes clés privées de lecture jusqu'à un
inventaire à zéro et ne jamais essayer toutes les clés.

La perte de la clé privée ne détruit pas automatiquement les enveloppes. Le
worker échoue fermé et l'exploitation décide soit de restaurer la clé, soit
d'exécuter la révocation globale confirmée qui impose une reconnexion.

En cas d'échec, conserver la nouvelle clé active et toutes les anciennes clés de lecture, corriger la cause, puis relancer. Ne jamais retirer une ancienne clé pour forcer la fin d'une rotation.

## Clé HPKE des credentials IMT

G5 peut sceller une enveloppe credential dans les tests, mais ne livre aucune
rotation opérationnelle et conserve l'enrôlement fermé en production. Le
`key_id` stocké permet un inventaire agrégé sans exposer la clé.

G6 devra définir une clé publique active, un keyring privé de lecture et une
rotation qui ne retire jamais une ancienne clé avant inventaire à zéro. Une clé
perdue ne doit pas être remplacée silencieusement : les enveloppes concernées
sont révoquées avec la commande hors réseau, puis les utilisateurs se
réenrôlent. Après restauration d'une base, la révocation globale utilise la
raison `database_restored` avant tout redémarrage normal.

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
