# Fabric

Fabric est un monorepo pour piloter des sessions locales et des providers distants via un backend Django et un agent Windows Python.

## Cadrage MVP

La cible produit retenue pour la suite est :

- `Fabric UI/API` accessible via Internet ;
- `Fabric Agent` exécuté localement sur un PC Windows ;
- une connexion sortante authentifiée de l'agent vers Fabric ;
- une **session Claude Code locale** comme cible réelle du pilotage.

Le modèle opérationnel est :

```text
Utilisateur distant
    |
    v
Fabric UI / API
    |
    | WebSocket sortant authentifié
    v
Fabric Agent sur PC Windows
    |
    v
Session Claude Code locale
```

Points de cadrage :

- aucun port entrant n'est requis sur le PC Windows ;
- Fabric n'utilise pas directement l'API Anthropic pour piloter la session utilisateur ;
- le navigateur et l'onglet `Code` de Claude Web sont des surfaces possibles, mais pas la définition du système ;
- le futur provider cible est `claude_code_local`.

## État actuel

Implémenté et vérifié de bout en bout :

- monorepo, backend Django + Channels, agent Python, frontend Angular ;
- modèles `Agent`, `Command`, `CommandEvent`, `Conversation`, `Message` ;
- protocole JSON versionné partagé, WebSocket agent authentifié par token ;
- provider **`claude_code_local`** : `session.status` et `message.send` avec
  streaming réel, continuité de session (`--resume`), options CLI
  (`--permission-mode`, `--model`, `--allowed-tools`) et annulation ;
- provider **`windows_powershell`** : sessions persistantes, opérations
  structurées et **commandes brutes** (shell distant complet — voir la note de
  sécurité ci-dessous) ;
- provider `echo` comme banc d'essai du transport ;
- terminal web unique qui route vers Claude ou PowerShell ;
- tests backend et agent, `ruff` et `mypy --strict` verts.

> **Sécurité — à lire avant toute exposition.** Un agent donne l'exécution de
> code arbitraire sur la machine où il tourne. Chaque agent appartient à un
> utilisateur : lui seul peut le voir, le piloter et émettre ses tokens — staff
> et superusers compris. Conséquences concrètes :
>
> - l'opérateur de prod est un utilisateur normal ; `/admin/` n'est pas exposé ;
> - servir Fabric en HTTPS et définir `SECRET_KEY` (obligatoire dès que `DEBUG`
>   est faux, tout comme `REDIS_URL`) ;
> - faire tourner l'agent sous un compte Windows non administrateur, par une
>   tâche planifiée à l'ouverture de session et non en service `LocalSystem` ;
> - les garde-fous sont décidés **sur le PC** par
>   `%APPDATA%\fabric-agent\policy.toml` ([agent/README.md](agent/README.md)) :
>   par défaut, PowerShell brut, `network.recover`, `bypassPermissions`,
>   `acceptEdits` et la désactivation du pont d'approbation sont refusés, quoi
>   que demande le serveur ;
>   Le terminal envoie par défaut `bypassPermissions` : l'agent refuse ces
>   tours tant que `policy.toml` ne liste pas ce mode dans
>   `allowed_permission_modes` ;
> - avec `claude:mode default`, Claude demande une autorisation avant chaque
>   outil et la question remonte dans le terminal (`allow` / `deny [raison]`) —
>   voir [docs/permission-bridge.md](docs/permission-bridge.md).
>
> Détail complet : [docs/audit-2026-07-29.md](docs/audit-2026-07-29.md).

## Authentification web (MFA TOTP obligatoire)

La connexion web exige **mot de passe + code TOTP** pour tout utilisateur :
aucun JWT n'est émis sans second facteur vérifié. L'agent du PC n'est pas
concerné (il s'authentifie par son jeton `Authorization: Bearer`).

| Étape | Endpoint | Réponse |
|---|---|---|
| 1. Mot de passe | `POST /api/v1/auth/login/` `{username, password[, remember_me]}` | `{mfa_required, mfa_token}` si l'utilisateur est enrôlé, sinon `{mfa_setup_required, mfa_token}` — jamais de jeton |
| 2a. Enrôlement | `POST /api/v1/auth/mfa/setup/` `{mfa_token}` | `{otpauth_uri, secret, qr_svg}` (QR en data URI SVG, même secret à chaque appel) |
| 2b. Confirmation | `POST /api/v1/auth/mfa/setup/confirm/` `{mfa_token, code}` | connexion normale (`access`, refresh en cookie si `X-Auth-Mode: cookie`) + `recovery_codes` (10, affichés **une seule fois**) |
| 2. Vérification | `POST /api/v1/auth/mfa/verify/` `{mfa_token, code}` | connexion normale ; `code` = TOTP (±1 pas) ou code de secours (usage unique) |
| Codes de secours | `POST /api/v1/auth/mfa/recovery-codes/` `{password}` (connecté) | 10 nouveaux codes, les anciens sont invalidés |

- TOTP RFC 6238 : SHA1, 6 chiffres, 30 s (compatible avec toutes les applis
  d'authentification). Un même pas de temps n'est jamais accepté deux fois.
- `mfa_token` : signé (`django.core.signing`, salé), lié à l'utilisateur et à un
  nonce, valable 5 min, consommé par la connexion qu'il termine. Le SPA le garde
  en mémoire uniquement.
- Erreurs génériques : `mfa_invalid_code`, `mfa_token_invalid` (400). Throttling
  de `verify/` et `setup/confirm/` par IP (`THROTTLE_MFA`, 5/min) **et** par
  compte (`THROTTLE_MFA_USER`, 5/min).
- Secrets TOTP chiffrés au repos (Fernet). La clé est dérivée par HKDF-SHA256 de
  `FABRIC_MFA_ENCRYPTION_KEY` si elle est définie, **sinon de `SECRET_KEY`**.
  Changer la source de la clé rend les secrets enrôlés illisibles : `reset_mfa`
  pour chaque utilisateur.
- Codes de secours : SHA-256 salé par code, seul le hash est stocké.
- Pas de désactivation depuis l'interface (MFA obligatoire). Changer le mot de
  passe conserve le MFA ; supprimer le compte supprime les données MFA.
- **Téléphone perdu / verrouillé** : `python manage.py reset_mfa <username>`
  efface le second facteur et révoque toutes les sessions ; la connexion
  suivante ré-enrôle un authentificateur.
- La mise en place du MFA (migration `api_auth.0004`) révoque tous les refresh
  tokens existants : chacun se reconnecte et s'enrôle.

## Structure

```text
fabric/
├── agent/
├── backend/
├── docker/
├── docs/
├── frontend/
└── shared/
```

## Démarrage rapide

Lanceur Windows simple :

1. double-cliquer [run-fabric-dev.cmd](run-fabric-dev.cmd)
2. pour tout arrêter, double-cliquer [stop-fabric-dev.cmd](stop-fabric-dev.cmd)

Le script :

- prépare les environnements si besoin ;
- applique les migrations ;
- crée ou met à jour l'utilisateur `fabric-admin` ;
- crée ou met à jour `demo-agent` ;
- démarre backend, frontend et agent dans trois fenêtres séparées.

Identifiants UI :

- username : `fabric-admin`
- password : `fabric-password`

Alternative manuelle :

1. `docker compose up -d`
2. `cd backend`
3. `python -m venv .venv`
4. `.venv\Scripts\pip install -e .`
5. `.venv\Scripts\python manage.py migrate`
6. `.venv\Scripts\python manage.py runserver`

Dans un autre terminal :

1. `cd backend`
2. `.venv\Scripts\python manage.py create_dev_agent --name demo-agent`
3. `.venv\Scripts\python manage.py create_dev_user`

Puis :

1. `cd agent`
2. `python -m venv .venv`
3. `.venv\Scripts\pip install -e .`
4. définir `FABRIC_SERVER_WS_URL`, `FABRIC_AGENT_ID`, `FABRIC_AGENT_TOKEN`
5. `.venv\Scripts\python -m fabric_agent`

Une fois connecté, le terminal accepte directement :

```text
claude:status                      sonde l'installation Claude Code de l'agent
claude Explique-moi ce repo        envoie un tour à Claude Code
open                               ouvre une session PowerShell
git status                         exécuté dans la session PowerShell
claude:on                          mode Claude : tout part à Claude, !<cmd> pour PowerShell
```

Voir :

- [docs/quickstart.md](docs/quickstart.md)
- [docs/claude-in-the-terminal.md](docs/claude-in-the-terminal.md)
- [docs/permission-bridge.md](docs/permission-bridge.md)
- [docs/agent-architecture.md](docs/agent-architecture.md)
- [docs/claude-code-local-mvp.md](docs/claude-code-local-mvp.md)
- [docs/claude-code-local-smoke-test.md](docs/claude-code-local-smoke-test.md)
- [docs/windows-powershell-provider.md](docs/windows-powershell-provider.md)
- [docs/protocol.md](docs/protocol.md)
- [docs/audit-2026-07-29.md](docs/audit-2026-07-29.md)
