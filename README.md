# IMTégrale

**Mes notes PASS, ma moyenne et mon GPA au même endroit.**

J'ai commencé IMTégrale parce que je voulais suivre mes résultats sans refaire les calculs à la main après chaque nouvelle note. Le projet est devenu un vrai tableau de bord multi-compte : il importe les notes et le profil depuis PASS, récupère les UE et ECTS officiels dans COMPETENCES, calcule les moyennes pondérées, garde le bot Telegram et peut prévenir dès qu'un résultat change.

[Ouvrir IMTégrale](https://imtegrale.tail4fed99.ts.net/) · [Voir la démo](https://imtegrale.tail4fed99.ts.net/demo) · [Comprendre la connexion](https://imtegrale.tail4fed99.ts.net/confiance)

IMTégrale est un projet étudiant indépendant. Il n'est ni affilié, ni approuvé par IMT Atlantique.

## Ce que fait le projet

- première importation depuis un compte PASS, puis connexion par passkey, compte IMT ou token personnel ;
- notes PASS, intitulés, semestres, grades et crédits ECTS officiels COMPETENCES, moyenne générale et GPA sur 4 ;
- actualisation manuelle ou automatique facultative, avec des quotas pour ne pas marteler PASS ;
- notification Telegram privée lorsqu'une note évolue ;
- partage en lecture seule avec des tokens révocables ;
- jusqu'à cinq simulations GPA et cinq simulations de notes privées, avec import académique, autosauvegarde, comparaison et hypothèses par semestre ;
- agenda de cours personnel importé depuis un lien iCalendar INPASS, conservé chiffré et actualisé chaque heure ;
- calendrier d'alternance 2026-2027 pour les FIP 2027, 2028 et 2029, avec périodes école, entreprise et mobilité ;
- leaderboard facultatif par promotion, avec deux classements : GPA par défaut et moyenne générale.

Le leaderboard utilise la moyenne calculée depuis les notes brutes PASS, le grade COMPETENCES lorsqu'il existe et les ECTS officiels. Lorsqu'un étudiant le rejoint, son identité PASS et ses deux scores deviennent immédiatement visibles par les participants actifs, tandis qu'il attend 48 heures avant de voir le moindre classement, rang ou nombre de participants. Il peut retirer ou effacer sa participation immédiatement puis revenir à tout moment ; chaque nouvelle activation relance intégralement les 48 heures d'attente avant consultation.

## Comment les données sont traitées

Le navigateur envoie les identifiants IMT à l'API via HTTPS. Le serveur les utilise pour la connexion CAS aux services PASS et COMPETENCES, puis chiffre le mot de passe avec AES-256-GCM lorsqu'une synchronisation future a été autorisée. Pour COMPETENCES, le jeton API créé depuis la session Shibboleth reste en mémoire, n'est envoyé qu'au Hub IMT et est révoqué après l'import. Le lien iCalendar INPASS est lui aussi chiffré, n'est jamais réaffiché et ne peut pas être consulté depuis un token de partage. Les tokens de partage ne sont pas conservés en clair et les sessions restent côté serveur dans des cookies `HttpOnly`.

PASS ne fournit pas ici de délégation OAuth. Le serveur doit donc pouvoir relire le secret chiffré pour synchroniser les notes ; une compromission simultanée de l'application et de sa clé maître permettrait de le déchiffrer. Cette limite est assumée et expliquée dans la [page de confiance](https://imtegrale.tail4fed99.ts.net/confiance).

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
- [actualisation automatique](docs/automatic-sync.md) et [synchronisation manuelle](docs/manual-sync.md) ;
- [déploiement et rollback](deploy/README.md) ;
- [politique de sécurité](SECURITY.md) et [guide de contribution](CONTRIBUTING.md).

Les exemples de déploiement doivent être adaptés à votre réseau. Les secrets, dumps, rapports d'audit et configurations rendues restent volontairement hors de Git.

## À propos

Le nom **IMTégrale** vient des calculs qu'on finit toujours par refaire autour des notes. Le projet a été développé sans connaître [PAFF de Lucien Hervé](https://pass.lucienherve.xyz/), mais son service étudiant est sorti avant ; le clin d'œil affiché dans l'application est donc volontaire.

Licence [MIT](LICENSE).
