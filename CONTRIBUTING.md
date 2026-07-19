# Contribuer à IMTégrale

IMTégrale manipule des identifiants et des résultats académiques. Toute contribution doit préserver l'isolation par compte, le consentement explicite et la protection de PASS contre les appels excessifs.

## Environnement local

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check backend
.venv/bin/ruff check --select S backend/app
.venv/bin/python scripts/check_secrets.py

cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm exec playwright install chromium
pnpm test:e2e
pnpm build
```

Utilisez uniquement des données fictives. Ne joignez ni capture PASS non anonymisée, ni `.env`, ni dump de base, ni configuration réseau rendue.

## Contributions à IMTégrale Parcours

Ce dépôt public ne doit contenir que le moteur générique, ses schémas publics et de petites fixtures marquées `DÉMO FICTIVE`. N'ajoutez jamais de document source réel, catalogue pédagogique réel, contenu dérivé, correction, illustration privée, index de recherche réel ou métadonnée permettant de les reconstituer. Cette interdiction couvre aussi `frontend/public`, `frontend/dist`, `backend/app/static`, les snapshots, les logs, les messages d'erreur et les artefacts CI.

Le dépôt privé frère `IMTegrale-Parcours-Private` possède les sources et le pipeline spécifique. Une release compilée n'est pas copiée dans le checkout public : en production, elle est installée sous `/opt/botnote-learning/releases/RELEASE_ID` et n'est lue qu'à travers l'API protégée. Aucun PDF ou archive pédagogique ne doit être ajouté à Git, même temporairement.

Toute contribution à cette frontière doit :

- refuser l'accès par défaut et appliquer la même dépendance d'autorisation au catalogue, au contenu, à la recherche, à la progression, aux sources, assets et téléchargements ;
- tester explicitement les contournements par compte anonyme, viewer, token `owner`, path traversal, symlink sortant et manifest invalide ;
- rendre du texte structuré sans MDX, HTML brut, script, iframe, URL arbitraire ou `dangerouslySetInnerHTML` non justifié formellement ;
- garder la recherche côté serveur et ne jamais envoyer l'index complet au navigateur ;
- utiliser exclusivement des identités et contenus synthétiques, avec PASS, HUB, INPASS, Telegram et Drive entièrement doublés dans les tests ;
- exécuter le garde anti-fuite et inspecter le diff ainsi que `frontend/dist` avant soumission.

La suite Playwright sous `frontend/e2e` intercepte toutes les routes API avec un
catalogue synthétique marqué `DÉMO FICTIVE` et bloque toute requête HTTP vers une
origine externe. N'y ajoutez jamais d'export, de capture ou d'identité provenant
d'un environnement réel. Les rapports et traces locaux restent ignorés par Git.

## Pull requests

- Limitez chaque pull request à un comportement cohérent et expliquez son impact utilisateur.
- Ajoutez des tests proportionnés au risque, en particulier pour l'authentification, les quotas, les autorisations et le classement.
- Signalez explicitement toute migration, nouvelle dépendance, donnée persistée ou modification du modèle de menace.
- Vérifiez le lint, les tests, le build, le scan de secrets et les audits de dépendances avant ouverture.
- Toute modification de la chaîne de release doit conserver le SBOM CycloneDX, le manifeste SHA-256, l'audit des frontières wheel/frontend et le smoke-test de l'artefact installé.

Les vulnérabilités ne doivent pas être proposées par pull request publique. Utilisez la procédure de [`SECURITY.md`](SECURITY.md).
