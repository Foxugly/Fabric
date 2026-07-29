# Protocole WebSocket Fabric v1.0

Tous les messages utilisent cette enveloppe :

```json
{
  "protocol_version": "1.0",
  "type": "command.request",
  "message_id": "uuid",
  "correlation_id": "uuid",
  "timestamp": "2026-07-29T09:30:00+02:00",
  "payload": {}
}
```

Types implémentés dans cette première livraison :

- `agent.hello`
- `agent.authenticated`
- `agent.heartbeat`
- `provider.capabilities`
- `command.request`
- `command.accepted`
- `command.started`
- `command.progress`
- `command.completed`
- `command.failed`
- `error`

Le protocole partagé est implémenté dans [shared/protocol/messages.py](/C:/Users/rvilain/PycharmProjects/Fabric/shared/protocol/messages.py).

Dans la première livraison, ce protocole transporte uniquement le provider de validation `echo`.

Le provider cible de la suite du produit est `claude_code_local`, qui réutilisera la même enveloppe et les mêmes événements de base pour le transport entre Fabric et l'agent local.
