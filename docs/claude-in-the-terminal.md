# Piloter Claude Code depuis le terminal Fabric

Le terminal Fabric route chaque ligne saisie vers l'une de trois destinations :

| Saisie | Destination |
|---|---|
| `help`, `clear`, `open`, `close`, `cancel`, `exit` | traité localement dans le navigateur |
| `claude …`, `claude:…` | provider `claude_code_local` sur l'agent |
| tout le reste | session PowerShell persistante de l'agent |

La partie Claude **ne nécessite pas de session PowerShell ouverte** : elle passe
par son propre provider.

## Commandes

```text
claude <prompt>     envoie un tour à la session Claude Code locale
claude              affiche le contexte courant (session, répertoire, mode)
claude:status       sonde l'installation Claude Code de la machine
claude:new          oublie l'identifiant de session : le prochain prompt en ouvre une neuve
claude:mode <mode>  change le mode de permission
allow / deny [why]  répond à une demande d'autorisation en attente
```

Modes de permission acceptés : `default` (défaut — demande via le pont
d'autorisation), `acceptEdits`, `plan`, `bypassPermissions`, `auto`, `manual`.

## Continuité de session

Claude Code renvoie un `session_id` à chaque tour. Le terminal le mémorise et le
repasse en `--resume` au tour suivant, ce qui donne une vraie conversation
continue. L'identifiant est persisté dans le `localStorage` avec le reste de
l'état du terminal, donc un rechargement de page ne perd pas le fil.

`claude:new` remet le compteur à zéro.

## Répertoire de travail

Le champ « working directory » de la barre du haut sert **aussi** de `cwd` pour
Claude : c'est ce qui détermine le projet sur lequel Claude travaille, quels
`CLAUDE.md` sont chargés et quels fichiers sont accessibles. Changer ce champ
change de projet.

## Autorisations

Le mode par défaut est `default` : Claude **demande** avant d'utiliser un outil,
et la question remonte dans le terminal.

```text
[?] Claude requests permission: Write — C:\Projects\demo\hello.txt
    allow           - run it
    deny [reason]   - refuse and tell Claude why
```

Le tour est réellement suspendu sur le PC pendant ce temps. La raison d'un refus
est transmise à Claude. Détail du mécanisme :
[permission-bridge.md](permission-bridge.md).

Pour ne plus être interrompu, `claude:mode acceptEdits` : les modifications de
fichiers passent sans demande. **À utiliser en connaissance de cause** — un
prompt envoyé depuis Fabric modifie alors des fichiers sans confirmation.
`claude:mode plan` donne un tour strictement en lecture.

## Affichage

Pendant un tour, le terminal affiche :

- le texte de la réponse au fil de l'eau (`message.delta`) ;
- une ligne `· <Outil>(<indice>)` à chaque appel d'outil (`message.tool_use`).

`Ctrl+C` ou le bouton *Cancel* annule le tour : le processus `claude` est tué
sur la machine Windows, et la commande passe en `cancelled`.

## Notifications

Le tour est réellement suspendu pendant une demande d'autorisation, donc si tu
n'es pas devant l'écran, il attend. Le bouton **Notifications** de la barre du
haut configure une cible PushIT pour être prévenu.

La configuration est **par utilisateur, en base** (modèle `PushItTarget`, calqué
sur `accounts.PushItTarget` de FoxRunner) : « qui notifier » suit le propriétaire
de la commande, pas une clé globale. Il faut le jeton `apt_…` d'une application
PushIT ; il est stocké en base et rendu à son propre propriétaire, pour que
l'éditeur puisse l'afficher — c'est un jeton d'émission seule, il ne permet pas
de lire les notifications.

Quatre événements, cochables indépendamment :

| Événement | Défaut | Pourquoi |
|---|:--:|---|
| Autorisation demandée | ✅ | le tour est bloqué jusqu'à la réponse |
| Tour Claude terminé | ✅ | avec le début de la réponse |
| Tour en échec | ✅ | échec, annulation, expiration |
| Agent déconnecté | ❌ | seulement si des commandes étaient en cours |

Volontairement limité à Claude : un `git status` PowerShell qui se termine ne
mérite pas de faire vibrer un téléphone.

Décocher **Activé** coupe les notifications sans perdre le jeton. Et si PushIT
est injoignable, Fabric continue : l'envoi est sur un fil séparé, les échecs sont
journalisés et jamais propagés à la commande.

Les variables `PUSHIT_*` côté SSM ne servent que de repli quand un utilisateur
n'a aucune cible.

## Limites connues

- Un tour = un processus `claude -p`. Les hooks, MCP et skills configurés
  localement s'appliquent, mais il n'y a pas de session interactive persistante.
- Si Claude pose une question ou demande une décision, le tour se termine avec
  la question comme réponse ; il faut répondre par un nouveau `claude <réponse>`.
- Pas de reprise après rechargement **pendant** qu'un tour est en cours côté
  Claude : seul l'état PowerShell est resynchronisé au retour.
- Le timeout par tour est de 600 s côté UI (plafond 3600 s côté agent).
