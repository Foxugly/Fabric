# Pont d'autorisation des outils Claude

## Le problème

En mode `-p` (non interactif), Claude Code ne peut pas afficher de demande
d'autorisation. Sans rien, tout outil qui en exige une est **refusé
silencieusement** et le tour se termine par « je n'ai pas pu ». Le contournement
habituel, `--permission-mode acceptEdits`, résout le blocage en supprimant la
question : Claude modifie alors les fichiers sans que personne ne valide.

Aucune des deux options n'est acceptable pour un outil qu'on pilote depuis une
page web : soit il ne sert à rien, soit il agit sans surveillance.

## Le mécanisme

Claude Code accepte `--permission-prompt-tool <outil MCP>` : au lieu d'afficher
un dialogue, il **appelle un outil MCP** et attend sa réponse. Fabric fournit
cet outil et le relaie jusqu'au navigateur.

```text
claude -p --permission-prompt-tool mcp__fabric__approval_prompt
   |
   | appel d'outil MCP (stdio)
   v
fabric_agent.permission_broker        processus lancé par claude lui-même
   |
   | socket loopback 127.0.0.1, jeton par exécution
   v
PermissionGateway                     dans l'agent Fabric
   |
   | session.action_required (WebSocket sortant)
   v
Backend Fabric                        PermissionRequest, commande -> waiting_user_action
   |
   | command.permission_request (WebSocket navigateur)
   v
Terminal web                          « allow » / « deny [raison] »
```

La décision repart en sens inverse : `POST /commands/<id>/permissions/<request_id>/`
→ `session.action_response` → la passerelle débloque le broker → le broker rend
à Claude `{"behavior": "allow"}` ou `{"behavior": "deny", "message": "…"}`.

Pendant tout ce temps, **le tour est réellement suspendu sur la machine** : le
processus `claude` attend la réponse de son outil MCP.

## Utilisation

Le mode de permission par défaut du terminal est `default`, c'est-à-dire :
demander. Quand Claude a besoin d'un outil, le terminal affiche

```text
[?] Claude requests permission: Write — C:\Projects\demo\hello.txt
    allow           - run it
    deny [reason]   - refuse and tell Claude why
```

et l'invite passe à `allow / deny ?`. La raison d'un refus est transmise à
Claude, qui en tient compte pour la suite du tour.

Pour ne plus être interrompu : `claude:mode acceptEdits` (les modifications de
fichiers passent sans demande). Pour un tour strictement en lecture :
`claude:mode plan`.

## Choix de conception

**Le broker est synchrone et sans dépendance.** Claude Code le lance lui-même ;
`asyncio` sur stdin/stdout de Windows est une source d'ennuis connue, et ce
processus ne traite qu'une question à la fois. Tout ce qui est écrit sur stdout
et qui n'est pas une réponse JSON-RPC casse le protocole : les diagnostics vont
sur stderr.

**La passerelle est en loopback avec un jeton par exécution.** Elle écoute sur
`127.0.0.1` sur un port éphémère ; toute connexion doit présenter le jeton
généré au démarrage de l'agent, sinon elle est fermée sans réponse.

**Rien ne s'auto-approuve.** Sans opérateur joignable — passerelle sans
gestionnaire, connexion à Fabric perdue, erreur de publication — la réponse est
`deny`. Une panne ne doit jamais se traduire par une autorisation.

**Aucun délai côté pont.** Un humain est au bout : c'est le `timeout_seconds`
du tour qui borne l'attente, et une déconnexion refuse tout ce qui est en
attente (`fail_pending`).

## Limites connues

- Le `timeout_seconds` du tour couvre **à la fois** le travail de Claude et le
  temps de réflexion humain. Le terminal envoie 600 s ; au-delà, le tour échoue.
  Côté serveur, `started_at` est réinitialisé à la réponse pour que le reaper ne
  compte pas l'attente, mais le processus `claude`, lui, garde son échéance.
- Une commande en `waiting_user_action` n'est jamais expirée par le temps : elle
  se termine par une réponse, une annulation, ou la déconnexion de l'agent.
- Une seule question à la fois est affichée dans le terminal. Claude Code
  sérialise ses demandes d'autorisation, donc c'est suffisant en pratique.
- `bypassPermissions` court-circuite entièrement le pont, par construction.
