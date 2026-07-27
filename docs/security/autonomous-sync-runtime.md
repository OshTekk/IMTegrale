# Runtime de synchronisation autonome

## Portée G6 et garde G7A

G6 termine le moteur serveur. G7A ajoute l'interface et un rollout serveur sans
ouvrir la production :

- `BOTNOTE_AUTONOMOUS_SYNC_ENABLED=false` ;
- `BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED=false` ;
- `BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT=off` ;
- allowlist canary vide ;
- l'API refuse toujours `autonomous` avec `409` ;
- aucun écran autonome n'est visible ;
- aucun compte ni credential réel n'est créé.

Le chemin devient techniquement activable uniquement sous une configuration
cohérente `canary` ou `all`, avec un propriétaire primaire autorisé, le
heartbeat worker prêt et le sealer public disponible. G7B reste requis pour
toute première activation réelle.

## Flux session-first

Le worker sync suit cet ordre :

1. réserver le job, le `SyncRequest`, le lease PASS et les quotas existants ;
2. essayer la session PASS/HUB HPKE ;
3. si elle fonctionne, ne jamais lire `imt_sync_credentials` ;
4. si elle est absente ou rejetée, vérifier le mode brut du compte ;
5. refuser `manual`, `session_only`, un runtime fermé, un compte hors rollout
   et tout acteur non propriétaire ou automatique ;
6. charger une photographie immuable de l'enveloppe active dans une transaction
   courte ;
7. relâcher les verrous SQL, ouvrir l'enveloppe dans le worker puis revérifier
   compte, mode, consentement, login, génération, enveloppe, leases et absence
   d'une nouvelle session ;
8. effectuer au maximum un SSO ;
9. revérifier les mêmes invariants après le retour réseau ;
10. verrouiller compte et credential jusqu'au commit qui stocke la session HPKE,
    applique les données et met à jour les métadonnées d'utilisation.

Le mot de passe n'entre jamais dans un job, une réponse, un événement, une
métrique ou un log. L'objet `OpenedImtPassword` a une représentation constante
et une durée de vie bornée au gateway. Python ne garantit toutefois pas la
zéroisation d'un objet `str`.

## Concurrence et révocation

La photographie lie :

- l'identifiant du compte et du credential ;
- le login IMT normalisé ;
- la génération du credential ;
- la version de consentement ;
- la version, le `key_id` et un digest en mémoire de l'enveloppe.

Toute divergence avant l'appel empêche le SSO. Toute divergence détectée après
l'appel jette les cookies, n'applique aucune note et ne marque pas le credential
de remplacement comme utilisé. Aucun verrou SQL n'est conservé pendant le
réseau.

Il subsiste une fenêtre irréductible entre la dernière vérification et l'envoi
du mot de passe : une requête déjà transmise ne peut pas être rappelée. La
vérification après retour empêche néanmoins sa persistance et l'application des
données.

## Classification des erreurs

| Incident | Credential | Compte | Retry immédiat |
| --- | --- | --- | --- |
| Mot de passe refusé | Enveloppe effacée, génération incrémentée, état `invalid` | Pause `credential_invalid` | Non |
| Enveloppe altérée | Même invalidation compare-and-swap | Pause `credential_invalid` | Non |
| Clé absente | Enveloppe conservée | Pause `credential_key_unavailable` | Non |
| Réseau, TLS ou PASS indisponible | Enveloppe et génération conservées, compteur d'échec augmenté si le secret a été transmis | Différé par les règles existantes | Non |
| Runtime fermé | Credential non lu | Pause `autonomous_runtime_unavailable` | Non |
| Compte hors rollout | Credential non lu | Pause `autonomous_runtime_unavailable` | Non |
| Révocation, remplacement ou lease perdu | Remplacement intact | Erreur `SYNC_AUTONOMOUS_STATE_CHANGED` | Non |

L'exception locale propriétaire reste après la session et après un credential
autonome applicable. Elle ne s'applique qu'au compte exact configuré et ne
contourne jamais une révocation autonome.

## Autorisations et isolation

Seul `botnote-sync-worker.service` reçoit les deux clés privées. Le web reçoit
uniquement la clé publique `pass-service-session`; scheduler, calendar et
outbox ne reçoivent aucune clé HPKE privée. Le rôle PostgreSQL `botnote-sync`
peut sélectionner la table credential et mettre à jour uniquement les colonnes
de cycle de vie nécessaires. `INSERT`, `DELETE`, `account_id`,
`consent_version`, DDL, rôles et Alembic restent refusés.

Une compromission du worker sync permet d'ouvrir les credentials actifs qu'il
peut lire. Cette conséquence est explicite : la séparation protège contre une
fuite de base ou une RCE web, pas contre une RCE du composant déchiffreur ni
contre root sur le LXC.

## Observabilité

`PassOperation.autonomous_credential_used` vaut `true` uniquement lorsque le
secret ouvert est réellement transmis au client IMT. Les métriques agrégées
comptent :

- opérations et succès utilisant un credential ;
- refus d'authentification et échecs transitoires ;
- invalidations ;
- sessions recréées ;
- SSO complets ;
- réutilisations de session après un SSO autonome ;
- fallback propriétaire local ;
- pauses par raison et durées.

Elles ne contiennent aucun login, compte, `key_id`, génération, taille
d'enveloppe, cookie ou longueur de mot de passe. G7A ajoute au portail
administrateur privé les seuls agrégats utiles au futur canary ; leur heure est
arrondie pour limiter l'identification indirecte d'un canary unique.

## Restauration

Une restauration de base peut ressusciter une enveloppe révoquée. Avant tout
redémarrage réseau, révoquer hors réseau toutes les sessions PASS/HUB et tous
les credentials IMT restaurés. Une clé perdue n'est jamais remplacée
silencieusement : restaurer la clé depuis son support distinct ou révoquer les
enveloppes et demander un futur réenrôlement.
