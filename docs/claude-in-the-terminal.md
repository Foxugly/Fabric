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
```

Modes de permission acceptés : `acceptEdits` (défaut), `plan`,
`bypassPermissions`, `auto`, `manual`, `default`.

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

## Mode de permission : le point important

En mode `-p` (non interactif), Claude Code ne peut pas afficher de demande
d'autorisation. Sans `--permission-mode`, tout outil qui en exige une est
simplement refusé, et Claude répond « je n'ai pas pu ». C'est pour cela que le
terminal envoie `acceptEdits` par défaut.

Conséquence à assumer : **un prompt envoyé depuis Fabric peut modifier des
fichiers sur le PC Windows sans confirmation**. Utilisez `claude:mode plan` pour
une session en lecture seule.

## Affichage

Pendant un tour, le terminal affiche :

- le texte de la réponse au fil de l'eau (`message.delta`) ;
- une ligne `· <Outil>(<indice>)` à chaque appel d'outil (`message.tool_use`).

`Ctrl+C` ou le bouton *Cancel* annule le tour : le processus `claude` est tué
sur la machine Windows, et la commande passe en `cancelled`.

## Limites connues

- Un tour = un processus `claude -p`. Les hooks, MCP et skills configurés
  localement s'appliquent, mais il n'y a pas de session interactive persistante.
- Si Claude pose une question ou demande une décision, le tour se termine avec
  la question comme réponse ; il faut répondre par un nouveau `claude <réponse>`.
- Pas de reprise après rechargement **pendant** qu'un tour est en cours côté
  Claude : seul l'état PowerShell est resynchronisé au retour.
- Le timeout par tour est de 600 s côté UI (plafond 3600 s côté agent).
