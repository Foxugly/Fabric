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
| `message.stream` | couverte par le streaming de `message.send` |
| `message.cancel` | couverte par `command.cancel` (le process `claude` est tué) |
| `session.attach` | implicite via `--resume <session_id>` — pas d'action dédiée |

Le provider `echo` reste comme banc d'essai du transport.

## Reste à faire

1. couche conversation : `apps/conversations` existe mais ne transmet ni
   `session_id` ni `working_directory` — chaque message y repart d'une session
   neuve, et aucune UI ne l'expose ;
2. `session.attach` et `message.cancel` comme actions de premier rang, ou
   retirer ces capabilities de la liste annoncée ;
3. propriété des agents et autorisation par utilisateur (prérequis avant toute
   exposition Internet) ;
4. remontée des demandes d'intervention manuelle (`waiting_user_action`) :
   le statut existe mais rien ne le produit.
