# IMTégrale

**Vos résultats, enfin réunis.**

IMTégrale est un service étudiant indépendant qui synchronise les notes PASS, calcule les moyennes pondérées par ECTS et le GPA, puis notifie Telegram. Il n'est ni affilié ni approuvé par IMT Atlantique ; PASS et IMT Atlantique sont cités uniquement pour identifier la source des données et l'établissement concerné.

Le projet est distribué sous licence MIT. Les contributions sont décrites dans [`CONTRIBUTING.md`](CONTRIBUTING.md) et les vulnérabilités doivent être signalées en privé selon [`SECURITY.md`](SECURITY.md).

Le classement facultatif propose deux vues indépendantes, GPA et moyenne générale. La participation publie l'identité officielle issue de PASS, les scores utilisent exclusivement les données brutes PASS pondérées par ECTS, et le classement reste entièrement masqué au nouvel inscrit pendant 48 heures.

## Architecture

- **Frontend** : React, TypeScript, Vite et TanStack Query.
- **API** : FastAPI, SQLAlchemy 2 et Alembic sur Python 3.11 stable.
- **Base** : PostgreSQL local, accessible uniquement par socket Unix et authentification peer.
- **Orchestration PASS** : passerelle globale en base, ordonnanceur embarqué dans l'API, quotas glissants et circuit breaker.
- **Temps réel** : Server-Sent Events avec reconnexion automatique.
- **Publication** : Nginx sur l'hôte frontal, Tailscale Funnel pour le site public, Serve pour l'administration privée et split-DNS facultatif pour le LAN.
- **Backend** : HTTPS mutuel entre le PVE et le LXC, avec certificats internes renouvelés automatiquement.
- **Administration** : portail distinct, limité à une identité Tailscale explicite, avec authentification séparée et journal d'audit.
- **Authentification** : compte IMT pour l'inscription et la vérification, puis passkey ou token propriétaire facultatifs sans appel à PASS.

## Sécurité

- Les mots de passe IMT et secrets Telegram sont chiffrés en AES-256-GCM avec une clé stockée hors base.
- Les tokens partagés ne sont jamais stockés en clair : seul un HMAC-SHA-256 est conservé.
- Chaque requête métier est filtrée par `account_id` côté serveur.
- Les sessions sont opaques, côté serveur, avec cookies `Secure`, `HttpOnly`, `SameSite=Strict` et protection CSRF.
- Les tokens disposent d'un rôle `owner`, `viewer` ou `editor`, d'une expiration et d'une révocation réévaluée pendant les flux SSE. Les passkeys ne stockent qu'une clé publique WebAuthn.
- Les synchronisations sont réservées au propriétaire, sérialisées globalement et espacées d'au moins 60 secondes. Chaque compte dispose de 3 opérations par heure et 8 par 24 heures ; les refus antérieurs au réseau ne consomment rien.
- Les authentifications IMT utilisent des délais progressifs par compte et par connexion cliente. Les indisponibilités amont ne pénalisent pas l'utilisateur, tandis qu'un circuit global suspend les appels lors d'une instabilité PASS.
- L'actualisation automatique est désactivée par défaut, exige un consentement explicite et ne démarre que les jours ouvrés entre 8 h et 20 h, au minimum toutes les deux heures. Le mode adaptatif ralentit après trois passages automatiques sans changement et revient à la cadence de base dès qu'une note évolue.
- Les notes, UE, sessions, événements, flux SSE et connexions PostgreSQL sont bornés par des quotas explicites.
- Le transport CAS/PASS valide l'origine HTTPS exacte à chaque requête et redirection, borne les durées et tailles, puis rejette les exports hors de l'espace PASS.
- Telegram refuse les redirections, borne les réponses et limite le nombre de messages envoyés par synchronisation.
- Le proxy est le seul client autorisé du backend : nftables limite `8443` au PVE et Uvicorn exige son certificat client.
- Les services systemd tournent sans privilèges avec un système de fichiers en lecture seule.
- Le leaderboard ne publie que le rang, le prénom et le nom officiels issus de PASS, ainsi que le score. Le cursus de primo-inscription et l'année de sortie attendue définissent un segment exact ; le campus reste un filtre serveur et ne figure jamais dans les lignes.
- Le campus est lu séparément dans la fiche PASS de chaque compte ; Rennes, Brest et Nantes disposent de filtres dédiés.
- L'administration est fermée par défaut, invisible depuis le LAN, liée à l'identité Tailscale autorisée et protégée par une session/CSRF dédiés.

Le chiffrement des identifiants permet au worker de les relire pour PASS. Une compromission complète du serveur et de sa clé maître permettrait donc leur déchiffrement ; ce n'est pas équivalent à un flux OAuth, que PASS ne fournit pas ici.

## Développement

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/pip-audit

cd frontend
pnpm install
pnpm test
pnpm build
```

## Déploiement

Le backend écoute uniquement en HTTPS sur l'adresse privée du conteneur. Nginx vérifie son certificat serveur interne, présente son propre certificat client et transmet une identité de débit construite à la frontière proxy. Tailscale Funnel ne publie que le listener loopback Nginx ; les routes d'administration exigent l'identité fournie par Serve et répondent `404` depuis Internet et le LAN. Le LAN peut utiliser le même certificat valide grâce au split-DNS.

Chaque version applicative vit sous `/opt/botnote/releases/<release>` et chaque environnement Python sous `/opt/botnote/venvs/<release>`. Les liens atomiques `current` et `runtime` permettent un rollback coordonné sans modifier l'environnement précédent. L'API héberge l'unique ordonnanceur PASS ; l'ancien timer `botnote-worker.timer` doit rester désactivé. Les unités systemd de `deploy/` gèrent l'API et les sauvegardes quotidiennes. La procédure complète est dans [`deploy/README.md`](deploy/README.md).

Les règles fonctionnelles, les garanties de confidentialité et la procédure d'administration du classement sont dans [`docs/leaderboard.md`](docs/leaderboard.md). Le consentement et l'ordonnancement des connexions planifiées à PASS sont décrits dans [`docs/automatic-sync.md`](docs/automatic-sync.md). Le délai, l'idempotence et la reprise des synchronisations manuelles sont spécifiés dans [`docs/manual-sync.md`](docs/manual-sync.md).

Les configurations reproductibles de durcissement se trouvent dans `deploy/security/`. Les rapports d'audit propres à une installation, les adresses du tailnet et les fichiers rendus restent volontairement hors du dépôt.

Sur un Wi-Fi local, `dnsmasq` peut résoudre uniquement le nom public d'IMTégrale vers l'adresse LAN du frontal et relayer les autres requêtes vers le routeur puis un résolveur de secours. Le client doit utiliser ce DNS local pour ce réseau : un DNS public configuré directement en parallèle pourrait être choisi à sa place et contourner la résolution locale.

L'identité `Tailscale-User-Login` n'est utilisée que sur le listener loopback réservé à Serve ; Funnel est identifié séparément par l'en-tête que Tailscale remplace lui-même. Le listener LAN reste indexé par adresse source et les limites globales utilisent une clé constante indépendante de l'adresse cliente.

La migration `backend/scripts/migrate_legacy.py` copie l'ancienne base SQLite vers PostgreSQL sans modifier la source.

L'identité publique est documentée dans [`docs/brand.md`](docs/brand.md). Les identifiants techniques historiques (`botnote`, `BOTNOTE_*`, `/opt/botnote` et `X-BotNote-Client-Identity`) sont volontairement conservés pour assurer la compatibilité des déploiements et ne constituent plus la marque affichée.
