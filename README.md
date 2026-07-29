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

Cette première livraison implémente uniquement :

- le squelette du monorepo ;
- le backend Django minimal ;
- les modèles `Agent`, `Command` et `CommandEvent` ;
- un protocole JSON versionné partagé ;
- un WebSocket agent authentifié par token de développement ;
- un agent Python minimal ;
- un provider factice `echo` avec progression ;
- les tests backend et agent ;
- la documentation de lancement local.

Le provider `claude_code_local` n'est pas encore implémenté dans cette livraison. Le provider `echo` sert de banc d'essai pour valider la chaîne complète `Fabric API -> agent local -> progression -> résultat`.

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

1. double-cliquer [run-fabric-dev.cmd](/C:/Users/rvilain/PycharmProjects/Fabric/run-fabric-dev.cmd)
2. pour tout arrêter, double-cliquer [stop-fabric-dev.cmd](/C:/Users/rvilain/PycharmProjects/Fabric/stop-fabric-dev.cmd)

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

Voir :

- [docs/quickstart.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/quickstart.md)
- [docs/claude-code-local-mvp.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/claude-code-local-mvp.md)
- [docs/windows-powershell-provider.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/windows-powershell-provider.md)
- [docs/protocol.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/protocol.md)
