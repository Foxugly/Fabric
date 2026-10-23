# Fabric agent (PC Windows)

L'agent se connecte en sortie à Fabric (`wss://…/ws/v1/agent/`) et exécute sur
le PC les commandes que le serveur lui transmet : tours Claude Code, PowerShell
et `echo`. Le serveur peut être compromis, de même que son Redis ou un jeton
volé. Les garde-fous sont donc décidés **sur le PC**, jamais par le serveur.

## Installation

```powershell
cd agent
py -3.12 -m venv .venv
.venv\Scripts\pip install -e .
```

## Configuration

| Variable | Rôle |
|---|---|
| `FABRIC_SERVER_WS_URL` | `wss://fabric.foxugly.com/ws/v1/agent/`. `ws://` est refusé (voir plus bas). |
| `FABRIC_AGENT_ID` | UUID de l'agent (`create_dev_agent` côté serveur). |
| `FABRIC_AGENT_TOKEN_FILE` | Fichier du jeton. Défaut : `%APPDATA%\fabric-agent\agent-token`. |
| `FABRIC_AGENT_TOKEN` | Repli **de développement** si le fichier du jeton n'existe pas. |
| `FABRIC_AGENT_POLICY_FILE` | Politique locale. Défaut : `%APPDATA%\fabric-agent\policy.toml`. |
| `FABRIC_AGENT_ALLOW_INSECURE` | `1` autorise `ws://`, **uniquement** vers `localhost` / `127.0.0.1` (dev). |
| `FABRIC_CLAUDE_CODE_EXECUTABLE` | Chemin de `claude.exe`. Les lanceurs `.cmd` / `.bat` sont refusés. |

Le jeton part dans l'en-tête `Authorization: Bearer <jeton>` du handshake
WebSocket. Il ne figure plus dans l'URL, qui finit dans les logs nginx.

### Jeton : fichier lisible par l'utilisateur seul

```powershell
.venv\Scripts\python -m fabric_agent.token_store
```

La commande lit le jeton sans écho, sans qu'il passe par la ligne de commande.
Elle l'écrit dans `%APPDATA%\fabric-agent\agent-token` et retire les ACL
héritées (`icacls /inheritance:r /grant:r <DOMAINE\utilisateur>:F`). Seul
l'utilisateur courant peut alors le lire. Ce fichier est prioritaire sur
`FABRIC_AGENT_TOKEN`. Sous Linux et macOS, l'agent refuse un fichier lisible
par d'autres que son propriétaire (mode différent de `600`).

Le jeton n'expire pas. Pour le faire tourner : `development-token` côté Fabric,
puis relancer `token_store`.

### Politique locale (`policy.toml`)

Sans fichier, l'agent applique les valeurs **les plus restrictives** :

- chaque appel d'outil de Claude passe par l'approbation dans Fabric : modes
  `default` et `plan` seulement, `bypassPermissions` et `acceptEdits` refusés,
  `permission_prompt: false` refusé, `allowed_tools` envoyés par le serveur
  refusés ;
- `updated_input` envoyé avec une approbation est ignoré : l'outil s'exécute
  avec l'entrée que l'opérateur a vue ;
- PowerShell brut (`command.run` avec `command`) désactivé. Les opérations
  structurées (`operation`) restent permises ;
- `network.recover` désactivé.

Toute demande hors politique est refusée avant exécution. Elle remonte à Fabric
en `command.failed` avec `policy_refused: true` et un message explicite, et
l'agent journalise un avertissement. Un fichier illisible ou une clé inconnue
**arrête** l'agent au démarrage : une faute de frappe ne passe pas inaperçue.

Exemple (toutes les clés sont facultatives) :

```toml
# %APPDATA%\fabric-agent\policy.toml
# Actions acceptées (défaut : tout le catalogue partagé).
allowed_actions = [
  "claude_code_local.session.status",
  "claude_code_local.message.send",
  "windows_powershell.session.create",
  "windows_powershell.session.status",
  "windows_powershell.session.close",
  "windows_powershell.command.run",
]
# Répertoires de travail permis (vide : pas de restriction).
working_directories = ["D:/Projects"]

[claude_code_local]
allowed_permission_modes = ["default", "plan"]
allow_disable_approval = false
allow_preapproved_tools = false
allow_updated_input = false

[windows_powershell]
allow_raw_commands = false
allow_network_recover = false
network_recover_adapters = []

[approvals]
# Exige que la réponse d'approbation renvoie l'empreinte de la demande.
require_fingerprint = false
```

Chaque garde-fou relâché est signalé par un avertissement au démarrage.

### Approbations

Chaque demande `session.action_required` porte un `fingerprint` : le SHA-256 de
`request_id`, `command_id`, du nom de l'outil et de son entrée exacte. L'agent
n'applique une réponse que si son `command_id` est celui de la demande et, si
elle porte un `fingerprint`, que celui-ci correspond. Une réponse qui ne
correspond pas est ignorée : la demande reste en attente.
`require_fingerprint = true` rend l'empreinte obligatoire. Ne l'activer qu'une
fois que le backend la renvoie.

## Lancement : tâche planifiée « à l'ouverture de session »

Ne **pas** installer l'agent en service Windows : un service tourne par défaut
en `LocalSystem`, avec plus de droits qu'un administrateur. Claude Code et les
transcripts appartiennent en outre à l'utilisateur. L'agent doit tourner sous
le compte de l'utilisateur connecté, idéalement non administrateur, via le
Planificateur de tâches :

```powershell
$agent = "D:\Projects\PycharmProjects\Fabric\agent"
$action = New-ScheduledTaskAction -Execute "$agent\.venv\Scripts\pythonw.exe" `
  -Argument "-m fabric_agent" -WorkingDirectory $agent
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Fabric agent" -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings
```

Les variables non secrètes (`FABRIC_SERVER_WS_URL`, `FABRIC_AGENT_ID`) se
définissent comme variables d'environnement **utilisateur** (`setx`). Le jeton
reste dans le fichier ci-dessus, jamais dans la tâche ni dans un `.cmd`.

## Développement

`run-fabric-dev.cmd` lance l'agent contre `ws://127.0.0.1:8000` avec
`FABRIC_AGENT_ALLOW_INSECURE=1`. Avec la politique par défaut, le PowerShell
brut y est refusé : créer un `policy.toml` de dev pour l'autoriser.

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\ruff check .
.venv\Scripts\mypy .
.venv\Scripts\pytest -q
```
