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
> utilisateur : lui seul (et les comptes `is_staff`) peut le voir, le piloter et
> émettre ses tokens. Conséquences concrètes :
>
> - ne donner `--staff` qu'à soi-même : un compte staff voit **tous** les agents ;
> - servir Fabric en HTTPS et définir `DJANGO_SECRET_KEY` (obligatoire hors
>   `DJANGO_DEBUG=true`) ;
> - faire tourner l'agent sous un compte Windows non administrateur ;
> - Claude demande une autorisation avant chaque outil et la question remonte
>   dans le terminal (`allow` / `deny [raison]`) — voir
>   [docs/permission-bridge.md](docs/permission-bridge.md).
>   `claude:mode acceptEdits` supprime ces demandes : à n'utiliser qu'en
>   connaissance de cause.
>
> Détail complet : [docs/audit-2026-07-29.md](docs/audit-2026-07-29.md).

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
