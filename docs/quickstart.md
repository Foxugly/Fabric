# Quickstart

Ce quickstart monte la chaîne complète et la valide avec le provider `echo`,
puis avec le vrai provider `claude_code_local`.

- cadrage produit : [claude-code-local-mvp.md](claude-code-local-mvp.md)
- usage au quotidien : [claude-in-the-terminal.md](claude-in-the-terminal.md)
- smoke test Claude réel : [claude-code-local-smoke-test.md](claude-code-local-smoke-test.md)

Le plus simple sous Windows reste `run-fabric-dev.cmd` à la racine, qui fait
les étapes 1 à 7 d'un coup. La procédure manuelle ci-dessous sert de référence.

## 1. Lancer l'infrastructure

```bash
docker compose up -d
```

## 2. Lancer le backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py runserver
```

## 3. Créer un utilisateur frontend

```bash
.venv\Scripts\python manage.py create_dev_user --staff
```

Identifiants par défaut :

- username: `fabric-admin`
- password: `fabric-password`

`--staff` donne les droits d'administration. Un compte staff voit et pilote
**tous** les agents de l'instance : ne l'accorder qu'à soi-même.

## 4. Créer un agent

L'utilisateur doit exister avant : un agent appartient à quelqu'un, et un agent
sans propriétaire n'est visible que du staff.

```bash
.venv\Scripts\python manage.py create_dev_agent --name demo-agent --owner fabric-admin
```

La commande retourne un `agent_id` et un `development_token`.

## 5. Lancer l'agent

```bash
cd ..\agent
python -m venv .venv
.venv\Scripts\pip install -e .
set FABRIC_SERVER_WS_URL=ws://127.0.0.1:8000/ws/v1/agent/
set FABRIC_AGENT_ID=<agent_id>
set FABRIC_AGENT_TOKEN=<development_token>
.venv\Scripts\python -m fabric_agent
```

## 6. Vérifier l'état

```bash
curl http://127.0.0.1:8000/api/v1/auth/login/ ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"fabric-admin\",\"password\":\"fabric-password\"}"
```

La réponse contient un token d'accès utilisateur.

## 7. Lancer le frontend

```bash
cd ..\frontend
npm install
npm start
```

L'interface est disponible sur `http://127.0.0.1:4200`.

## 8. Vérifier l'état agent via API

```bash
curl http://127.0.0.1:8000/api/v1/agents/ ^
  -H "Authorization: Token <user_token>"
```

L'agent doit être `online`.

## 9. Envoyer une commande echo

```bash
curl -X POST http://127.0.0.1:8000/api/v1/commands/ ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Token <user_token>" ^
  -d "{\"agent_id\":\"<agent_id>\",\"provider\":\"echo\",\"action\":\"echo.message.send\",\"payload\":{\"text\":\"Bonjour Fabric\"}}"
```

La réponse retourne `command_id`.

## 10. Suivre la progression

```bash
curl http://127.0.0.1:8000/api/v1/commands/<command_id>/ ^
  -H "Authorization: Token <user_token>"
```

Le statut doit évoluer `pending -> dispatched -> running -> succeeded`, et les `events` doivent contenir les deltas de progression.

## 11. Envoyer un vrai tour Claude Code

```bash
curl -X POST http://127.0.0.1:8000/api/v1/commands/ ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Token <user_token>" ^
  -d "{\"agent_id\":\"<agent_id>\",\"provider\":\"claude_code_local\",\"action\":\"claude_code_local.message.send\",\"timeout_seconds\":300,\"payload\":{\"text\":\"Reply with exactly: FABRIC_SMOKE_OK\",\"working_directory\":\"C:\\\\path\\\\to\\\\repo\",\"permission_mode\":\"plan\"}}"
```

`result.text` doit contenir `FABRIC_SMOKE_OK` et `result.session_id` l'identifiant
à repasser dans le `payload` du tour suivant pour continuer la conversation.

Depuis l'interface, la même chose s'écrit `claude Reply with exactly: FABRIC_SMOKE_OK`.
