# IMTégrale

**Mes notes PASS, ma moyenne et mon GPA au même endroit.**

J'ai commencé IMTégrale parce que je voulais suivre mes résultats sans refaire les calculs à la main après chaque nouvelle note. Le projet est devenu un vrai tableau de bord multi-compte : il importe les notes et le profil depuis PASS, récupère les UE et ECTS officiels dans COMPETENCES, calcule les moyennes pondérées, garde le bot Telegram et peut prévenir dès qu'un résultat change.

[Ouvrir IMTégrale](https://imtegrale.tail4fed99.ts.net/) · [Voir la démo](https://imtegrale.tail4fed99.ts.net/demo) · [Comprendre la connexion](https://imtegrale.tail4fed99.ts.net/confiance)

IMTégrale est un projet étudiant indépendant. Il n'est ni affilié, ni approuvé par IMT Atlantique.

## Ce que fait le projet

- première importation depuis un compte PASS sans conserver le mot de passe IMT, puis connexion par passkey, compte IMT ou token personnel ;
- notes PASS, intitulés, semestres, grades et crédits ECTS officiels COMPETENCES, moyenne générale et GPA sur 4 ;
- actualisation manuelle ou automatique facultative, avec des quotas pour ne pas marteler PASS ;
- notification Telegram privée lorsqu'une note évolue ;
- partage en lecture seule avec des tokens révocables ;
- jusqu'à cinq simulations GPA et cinq simulations de notes privées, avec import académique, autosauvegarde, comparaison et hypothèses par semestre ;
- agenda de cours personnel importé depuis un lien iCalendar INPASS, conservé chiffré et actualisé chaque heure ;
- calendrier d'alternance 2026-2027 pour les FIP 2027, 2028 et 2029, avec périodes école, entreprise et mobilité ;
- relevé académique personnel en PDF, filtrable par semestre, avec provenance PASS/COMPETENCES, annexe facultative et lien vers le code source ;
- leaderboard facultatif par promotion, avec deux classements : GPA par défaut et moyenne générale.

Le leaderboard utilise la moyenne calculée depuis les notes brutes PASS, le grade COMPETENCES lorsqu'il existe et les ECTS officiels. Lorsqu'un étudiant le rejoint, son identité PASS et ses deux scores deviennent immédiatement visibles par les participants actifs, tandis qu'il attend 48 heures avant de voir le moindre classement, rang ou nombre de participants. Il peut retirer ou effacer sa participation immédiatement puis revenir à tout moment ; chaque nouvelle activation relance intégralement les 48 heures d'attente avant consultation.

## Comment les données sont traitées

Le navigateur envoie les identifiants IMT à l'API via HTTPS pour l'authentification CAS. Le mot de passe sert uniquement à cette opération et n'est jamais écrit en base. Après la connexion, IMTégrale ne conserve que les cookies sécurisés strictement nécessaires aux domaines PASS et Hub : ils sont filtrés, chiffrés en AES-256-GCM, révocables et supprimés au plus tard après 30 jours. Si cette session expire plus tôt, l'actualisation automatique se met en pause jusqu'à une reconnexion volontaire.

Pour COMPETENCES, le jeton API créé depuis la session Shibboleth reste en mémoire, n'est envoyé qu'au Hub IMT et est révoqué après l'import. Le lien iCalendar INPASS est lui aussi chiffré, n'est jamais réaffiché et ne peut pas être consulté depuis un token de partage. Les tokens de partage ne sont pas conservés en clair et les sessions web utilisent des cookies `HttpOnly`.

PASS ne fournit pas ici de délégation OAuth. Le worker doit donc pouvoir déchiffrer une session PASS/HUB active pour synchroniser ; une compromission simultanée de l'application et de sa clé maître permettrait de l'utiliser jusqu'à son expiration ou sa révocation, mais pas de retrouver le mot de passe IMT. Une instance auto-hébergée peut, de façon explicite, réserver à son unique compte propriétaire un secret local hors base et hors dépôt, lisible seulement par l'utilisateur système du service. Cette exception est désactivée par défaut et n'est jamais proposée aux comptes publics. Ces limites sont expliquées dans la [page de confiance](https://imtegrale.tail4fed99.ts.net/confiance).

## Consentement et cadre d'usage

Le choix d'un étudiant permet à IMTégrale de traiter ses données pour les fonctions demandées. Il ne transforme pas le projet en service officiel et ne remplace ni l'autorisation de l'exploitant de PASS, ni la charte informatique signée par l'étudiant. Le [règlement intérieur d'IMT Atlantique](https://www.imt-atlantique.fr/sites/default/files/ecole/ddrs/odd/ODD%2016/REGLEMENT-INTERIEUR-IMT%20A%202023-version-suite-CE-14-avril-2023.pdf) rend cette charte applicable ; la [charte RENATER](https://www.renater.fr/wp-content/uploads/2022/01/charte-de-bon-usage-de-linformatique-et-du-reseau-renater.pdf) citée par l'école rappelle notamment le caractère personnel de l'accès et l'usage rationnel des ressources.

L'exploitant d'une instance décide du fonctionnement et des finalités du service : le consentement utilisateur ne lui transfère donc pas toute la responsabilité. Avant une ouverture publique, il doit vérifier les règles de l'établissement, définir une base légale par finalité, publier son identité et un contact, annoncer les durées de conservation et organiser l'exercice des droits. La [cartographie des données et des choix](docs/data-and-usage.md) décrit ce que le logiciel fait réellement ; elle ne constitue pas un avis juridique ni une homologation par IMT Atlantique.

## Stack

- React 19, TypeScript, Vite, TanStack Query, Recharts et FullCalendar ;
- FastAPI, SQLAlchemy 2, Alembic et PostgreSQL ;
- Server-Sent Events pour pousser les changements vers l'interface ;
- Nginx, mTLS entre le frontal et le LXC, Tailscale Serve/Funnel et systemd en production.

## Lancer le projet en local

Prérequis : Python 3.11+, Node.js 22+ et `pnpm`.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.development.example .env

# Remplacer les deux valeurs correspondantes dans .env.
.venv/bin/python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"

.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8080
```

Dans un second terminal :

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

L'interface est alors disponible sur `http://127.0.0.1:5173`. Utilisez uniquement des données fictives pour développer ou contribuer.

## Vérifications

```bash
.venv/bin/ruff check backend
.venv/bin/pytest
.venv/bin/pip-audit

cd frontend
pnpm typecheck
pnpm test
pnpm build
pnpm audit --prod
```

## Documentation

- [classement, confidentialité et modération](docs/leaderboard.md) ;
- [simulations GPA et simulations de notes privées](docs/simulations.md) ;
- [agenda INPASS et calendrier de formation FIP](docs/calendars.md) ;
- [relevé académique personnel et transparence](docs/academic-report.md) ;
- [données, consentements et cadre d'utilisation](docs/data-and-usage.md) ;
- [actualisation automatique](docs/automatic-sync.md) et [synchronisation manuelle](docs/manual-sync.md) ;
- [déploiement et rollback](deploy/README.md) ;
- [politique de sécurité](SECURITY.md), [historique des versions](CHANGELOG.md) et [guide de contribution](CONTRIBUTING.md).

Les exemples de déploiement doivent être adaptés à votre réseau. Les secrets, dumps, rapports d'audit et configurations rendues restent volontairement hors de Git.

## À propos

Le nom **IMTégrale** vient des calculs qu'on finit toujours par refaire autour des notes. Le projet a été développé sans connaître [PAFF de Lucien Hervé](https://pass.lucienherve.xyz/), mais son service étudiant est sorti avant ; le clin d'œil affiché dans l'application est donc volontaire.

Licence [MIT](LICENSE).
