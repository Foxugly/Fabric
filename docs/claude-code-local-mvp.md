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

Cette première livraison n'implémente pas encore `claude_code_local`.

Le socle actuellement disponible couvre :

- le monorepo ;
- le backend Django ;
- le WebSocket agent ;
- l'authentification de développement par token ;
- le protocole JSON versionné ;
- l'agent Python minimal ;
- le provider factice `echo` ;
- les tests backend et agent ;
- la documentation de lancement.

Le provider `echo` sert à valider la chaîne technique complète avant d'ajouter l'adapter réel vers Claude Code local.

## Ordre de travail recommandé

1. conserver `echo` comme banc d'essai ;
2. ajouter les concepts métier restants autour des conversations et messages ;
3. concevoir l'adapter `claude_code_local` ;
4. seulement ensuite brancher une surface Claude Code réelle.
