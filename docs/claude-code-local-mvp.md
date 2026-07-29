# Claude Code Local MVP

## Objectif

Le MVP cible une **session Claude Code locale** exécutée sur un PC Windows appartenant à l'utilisateur.

Fabric sert d'interface distante entre Internet et l'agent local :

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

## Contraintes

- aucun port entrant sur le PC Windows ;
- l'agent initie la connexion vers Fabric ;
- aucune tentative de contournement d'authentification, MFA, CAPTCHA ou politique de sécurité ;
- la session locale reste la source de vérité ;
- le navigateur et l'onglet `Code` de Claude Web sont des surfaces possibles, mais pas le cœur du modèle.

## Provider cible

Le provider cible de la suite du projet est :

```text
claude_code_local
```

Ce provider représentera une session Claude Code locale active ou activable sur la machine Windows.

Capabilities visées :

```json
{
  "provider": "claude_code_local",
  "capabilities": [
    "session.status",
    "session.attach",
    "message.send",
    "message.stream",
    "message.cancel"
  ]
}
```

## Responsabilités attendues

Le futur provider `claude_code_local` devra :

- détecter une session locale utilisable ;
- s'y attacher ;
- envoyer une instruction ;
- suivre l'exécution ;
- transmettre les deltas de progression ;
- renvoyer le résultat final ;
- signaler toute intervention manuelle requise.

## Résultat attendu de `session.status`

Exemple de réponse cible :

```json
{
  "session_detected": true,
  "session_ready": true,
  "manual_action_required": false,
  "transport": "local_session"
}
```

Si la session n'est pas exploitable, l'agent doit renvoyer un état explicite :

```json
{
  "status": "waiting_user_action",
  "action_required": {
    "type": "local_session_setup",
    "provider": "claude_code_local",
    "message": "Une session Claude Code locale doit être ouverte ou réautorisée sur le PC Windows."
  }
}
```

## Périmètre actuel

`claude_code_local` est implémenté et validé de bout en bout via la CLI
`claude` en mode `-p` :

| Capability | État |
|---|---|
| `session.status` | implémentée (détection conservatrice, cf. `detector.py`) |
| `message.send` | implémentée, avec streaming réel des deltas |

Le streaming (`message.stream`), l'annulation (`message.cancel`) et l'attache
(`session.attach`) ne sont pas des actions distinctes : le streaming est le mode
normal de `message.send`, l'annulation passe par le message protocole générique
`command.cancel`, et l'attache est implicite via `--resume <session_id>`. Ces
trois noms étaient annoncés en capabilities sans exister ; ils ont été retirés.

Le provider `echo` reste comme banc d'essai du transport.

## Reste à faire

1. **pont d'autorisation des outils** : brancher `--permission-prompt-tool` sur
   un serveur MCP local pour que les demandes d'autorisation remontent dans
   l'interface web, au lieu du `acceptEdits` global. `waiting_user_action`
   existe déjà côté modèle pour porter cet état ;
2. **session persistante** via `--input-format stream-json`, au lieu d'un
   processus `claude -p` par tour ;
3. **interface de conversation** : `apps/conversations` transmet désormais la
   session d'un tour au suivant, mais aucune UI ne l'expose.
