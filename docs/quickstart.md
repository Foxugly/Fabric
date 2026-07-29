# Quickstart

Ce quickstart valide la première livraison technique avec le provider factice `echo`.

Le provider cible métier pour la suite du projet est `claude_code_local`, documenté dans [docs/claude-code-local-mvp.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/claude-code-local-mvp.md). Il n'est pas encore implémenté à ce stade.

Le smoke test réel pour `claude_code_local` est documenté dans [docs/claude-code-local-smoke-test.md](/C:/Users/rvilain/PycharmProjects/Fabric/docs/claude-code-local-smoke-test.md).

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

## 3. Créer un agent

```bash
.venv\Scripts\python manage.py create_dev_agent --name demo-agent
```

La commande retourne un `agent_id` et un `development_token`.

## 4. Créer un utilisateur frontend

```bash
.venv\Scripts\python manage.py create_dev_user
```

Identifiants par défaut :

- username: `fabric-admin`
- password: `fabric-password`

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
