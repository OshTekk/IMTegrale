# Contribuer à IMTégrale

IMTégrale manipule des identifiants et des résultats académiques. Toute contribution doit préserver l'isolation par compte, le consentement explicite et la protection de PASS contre les appels excessifs.

## Environnement local

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check backend

cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

Utilisez uniquement des données fictives. Ne joignez ni capture PASS non anonymisée, ni `.env`, ni dump de base, ni configuration réseau rendue.

## Pull requests

- Limitez chaque pull request à un comportement cohérent et expliquez son impact utilisateur.
- Ajoutez des tests proportionnés au risque, en particulier pour l'authentification, les quotas, les autorisations et le classement.
- Signalez explicitement toute migration, nouvelle dépendance, donnée persistée ou modification du modèle de menace.
- Vérifiez le lint, les tests, le build et les audits de dépendances avant ouverture.

Les vulnérabilités ne doivent pas être proposées par pull request publique. Utilisez la procédure de [`SECURITY.md`](SECURITY.md).
