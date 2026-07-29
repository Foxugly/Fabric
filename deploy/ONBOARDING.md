# Onboarding Fabric dans la flotte EC2

Suit `OPERATIONS.md §3.12`. Fabric est le **deuxième site ASGI** de la flotte
(après poker) et le premier à tenir un WebSocket sortant permanent depuis un PC.

| Choix | Valeur | Pourquoi |
|---|---|---|
| Port backend | `127.0.0.1:8008` | 8000-8007 pris (§3.4) |
| Unité | `fabric-asgi` (daphne) | Channels exige un serveur ASGI (§3.11) |
| Redis | DB **5** | db0-db4 déjà utilisées |
| Vhost | `fabric.foxugly.com` — **un seul** | même origine SPA + API + WS : pas de CORS, pas de sub_filter, pas de validateur d'origine cross-host |
| TLS | wildcard `foxugly.com` | jamais de certbot par sous-domaine (§3.6) |
| Modèle build | S3-bundle pour la SPA, git-on-box pour le backend | node existe sur la box mais on évite `npm install` en production |
| DB | PostgreSQL locale, rôle+base `fabric` | §3.13 |

Valeurs déjà relevées, pas besoin de les chercher :

```text
repo_id      1315830386          (Foxugly/Fabric, public)
owner_id     3275928             (Foxugly)
instance_id  i-0fe664678563bae5f
public_ip    54.229.220.110
```

> Le job `deploy` reste **skipped** tant que le secret `AWS_DEPLOY_ROLE_ARN`
> n'existe pas : merger avant d'avoir fait les étapes 1-4 ne casse rien.

---

## 1. CloudShell — secrets SSM

Les secrets sont générés **dans CloudShell** : ils ne passent jamais ailleurs.

```bash
REGION=eu-west-1
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

put() { aws ssm put-parameter --region "$REGION" --name "/fabric/prod/$1" \
          --value "$2" --type "${3:-String}" --overwrite >/dev/null && echo "  $1"; }

# Secrets (SecureString)
put SECRET_KEY  "$SECRET_KEY"  SecureString
put DB_PASSWORD "$DB_PASSWORD" SecureString

# Config (String)
put STATE                 PROD
put DEBUG                 false
put ALLOWED_HOSTS         fabric.foxugly.com
put CSRF_TRUSTED_ORIGINS  https://fabric.foxugly.com
put CORS_ALLOWED_ORIGINS  https://fabric.foxugly.com
put FRONTEND_BASE_URL     https://fabric.foxugly.com
put LOG_LEVEL             INFO
put REDIS_URL             redis://127.0.0.1:6379/5
put THROTTLE_LOGIN        10/hour
put FABRIC_TOKEN_TTL_HOURS 168

# DB_* 6-var convention (§3.13)
put DB_ENGINE postgresql
put DB_HOST   127.0.0.1
put DB_PORT   5432
put DB_NAME   fabric
put DB_USER   fabric

echo "--- relis le mot de passe DB une fois, tu en auras besoin a l'etape 5 ---"
echo "DB_PASSWORD=$DB_PASSWORD"
```

`SENTRY_DSN` s'ajoute à l'étape 7, une fois le projet créé.

Vérification :

```bash
aws ssm describe-parameters --region eu-west-1 \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/fabric/prod" \
  --query 'Parameters[].{Name:Name,Type:Type}' --output table
```

Attendu : 16 paramètres, `SECRET_KEY` et `DB_PASSWORD` en `SecureString`.

---

## 2. CloudShell — autoriser le rôle d'instance à lire `/fabric/prod`

Le grant est **par préfixe** (§3.5), et `GetParametersByPath` exige **les deux**
ARN : le nœud *et* les enfants. On édite l'inline policy existante sans écraser
les préfixes des autres sites.

```bash
ROLE=foxugly-fleet-ec2
POLICY=foxugly-fleet-app-config
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

aws iam get-role-policy --role-name "$ROLE" --policy-name "$POLICY" \
  --query PolicyDocument > /tmp/fleet-policy.json
cp /tmp/fleet-policy.json /tmp/fleet-policy.bak.json

NODE="arn:aws:ssm:eu-west-1:${ACCOUNT}:parameter/fabric/prod"
CHILDREN="arn:aws:ssm:eu-west-1:${ACCOUNT}:parameter/fabric/prod/*"

jq --arg node "$NODE" --arg children "$CHILDREN" '
  .Statement |= map(
    if ((.Action | if type=="array" then . else [.] end)
         | index("ssm:GetParametersByPath"))
    then .Resource = ((.Resource | if type=="array" then . else [.] end)
                      + [$node, $children] | unique)
    else . end)
' /tmp/fleet-policy.json > /tmp/fleet-policy.new.json

# RELIS le diff avant d'appliquer : c'est la policy de TOUTE la flotte.
diff <(jq -S . /tmp/fleet-policy.bak.json) <(jq -S . /tmp/fleet-policy.new.json)
```

Si le diff n'ajoute que les deux ARN `fabric` :

```bash
aws iam put-role-policy --role-name "$ROLE" --policy-name "$POLICY" \
  --policy-document file:///tmp/fleet-policy.new.json
```

> Si le `diff` est vide, aucun statement ne portait `ssm:GetParametersByPath` :
> inspecte `/tmp/fleet-policy.json` à la main plutôt que de forcer.

---

## 3. CloudShell — rôle de déploiement OIDC `fabric-deploy`

⚠️ **Fabric est un dépôt récent : le claim `sub` est qualifié par les IDs
immuables** (§3.11 GOTCHA). On épingle **les deux formes** — `StringEquals` avec
une liste est un OU logique, donc ça reste exact et sans joker.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO_ID=1315830386
OWNER_ID=3275928
INSTANCE_ID=i-0fe664678563bae5f
echo "repo_id=$REPO_ID  instance=$INSTANCE_ID  account=$ACCOUNT"

cat > /tmp/fabric-trust.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": [
          "repo:Foxugly@${OWNER_ID}/Fabric@${REPO_ID}:environment:production",
          "repo:Foxugly/Fabric:environment:production"
        ]
      }
    }
  }]
}
JSON

aws iam create-role --role-name fabric-deploy \
  --assume-role-policy-document file:///tmp/fabric-trust.json \
  --description "Fabric deploy (OIDC from Foxugly/Fabric, environment production)"

cat > /tmp/fabric-deploy-policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "RunDeployCommand",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:eu-west-1:${ACCOUNT}:instance/${INSTANCE_ID}",
        "arn:aws:ssm:eu-west-1::document/AWS-RunShellScript"
      ]
    },
    {
      "Sid": "ReadCommandResult",
      "Effect": "Allow",
      "Action": "ssm:GetCommandInvocation",
      "Resource": "*"
    },
    {
      "Sid": "ShipTheSpaBundle",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::foxugly-deploy/builds/fabric-frontend/*"
    }
  ]
}
JSON

aws iam put-role-policy --role-name fabric-deploy \
  --policy-name fabric-deploy --policy-document file:///tmp/fabric-deploy-policy.json

echo "AWS_DEPLOY_ROLE_ARN = arn:aws:iam::${ACCOUNT}:role/fabric-deploy"
echo "EC2_INSTANCE_ID     = ${INSTANCE_ID}"
```

Reporte ces deux valeurs dans les secrets du dépôt GitHub (`Settings → Secrets
and variables → Actions`), et crée l'environnement `production` (`Settings →
Environments`) — le job `deploy` tourne sous `environment: production`, le trust
l'exige.

---

## 4. CloudShell — DNS

```bash
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name foxugly.com. \
  --query 'HostedZones[0].Id' --output text | sed 's#/hostedzone/##')
IP=54.229.220.110
echo "zone=$ZONE_ID ip=$IP"

aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{
     \"Name\":\"fabric.foxugly.com\",\"Type\":\"A\",\"TTL\":300,
     \"ResourceRecords\":[{\"Value\":\"$IP\"}]}}]}"
```

Le wildcard `*.foxugly.com` couvre déjà ce nom : **aucun certbot à lancer** (§3.6).

---

## 5. Sur la box — base PostgreSQL

À faire une fois l'étape 1 terminée (le mot de passe vient de SSM).

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE fabric LOGIN PASSWORD '<DB_PASSWORD de l'etape 1>';
CREATE DATABASE fabric OWNER fabric;
SQL
sudo -u postgres psql -d fabric -c 'ALTER SCHEMA public OWNER TO fabric'
```

## 6. Sur la box — bootstrap unique

Le pipeline suppose que le dépôt existe déjà (§3.11 gotcha 5).

```bash
sudo -u django git clone https://github.com/Foxugly/Fabric.git \
  /var/www/django_websites/Fabric
sudo git config --global --add safe.directory /var/www/django_websites/Fabric
sudo -u django python3 -m venv /var/www/django_websites/Fabric/backend/.venv

# Unités + sudoers : root, hors-bande, depuis le blob git (jamais depuis l'arbre)
cd /var/www/django_websites/Fabric
sudo git show origin/main:deploy/fetch-env-from-ssm.sh | sudo tee /usr/local/sbin/fabric-env-fetch.sh >/dev/null
sudo chown root:root /usr/local/sbin/fabric-env-fetch.sh
sudo chmod 0755 /usr/local/sbin/fabric-env-fetch.sh
sudo git show origin/main:deploy/systemd/fabric-env-fetch.service | sudo tee /etc/systemd/system/fabric-env-fetch.service >/dev/null
sudo git show origin/main:deploy/systemd/fabric-asgi.service | sudo tee /etc/systemd/system/fabric-asgi.service >/dev/null
sudo git show origin/main:deploy/sudoers/fabric-deploy | sudo tee /etc/sudoers.d/fabric-deploy >/dev/null
sudo chown root:root /etc/sudoers.d/fabric-deploy
sudo chmod 0440 /etc/sudoers.d/fabric-deploy
sudo visudo -c

sudo systemctl daemon-reload
sudo systemctl enable --now fabric-env-fetch
sudo head -c 40 /run/fabric/.env   # doit montrer des variables, pas une erreur
sudo systemctl enable fabric-asgi
```

## 7. Sentry (§3.8)

Créer les projets `fabric-backend` et `fabric-frontend` (org `foxugly-srl`,
région `de.sentry.io`), puis :

```bash
aws ssm put-parameter --region eu-west-1 --name /fabric/prod/SENTRY_DSN \
  --value '<dsn backend>' --type String --overwrite
aws ssm put-parameter --region eu-west-1 --name /fabric/prod/SENTRY_ENVIRONMENT \
  --value PROD --type String --overwrite
sudo systemctl restart fabric-env-fetch fabric-asgi   # sur la box
```

> **Sentry frontend : pas encore câblé.** La SPA n'a pas de mécanisme de config
> runtime (choix assumé du vhost unique : pas de `sub_filter`). Le brancher
> demandera soit un `/fabric-frontend/prod` + injection, soit un DSN à la
> compilation. C'est le seul point de §3.12 non couvert.

## 8. Monitoring (§3.9)

Un monitor UptimeRobot HTTP sur `https://fabric.foxugly.com/health/`, assertion
mot-clé `"status": "ok"`.

## 9. Vérification finale (§3.12.9)

```bash
curl -s https://fabric.foxugly.com/health/            # 200 + status ok
sudo find /var/www/django_websites/Fabric ! -type l \( -perm /020 -o -perm /004 \) | head
sudo -l -U django | grep -i fabric
sudo ss -ltnp | grep 8008
```

La première ligne doit renvoyer `{"status": "ok", …}`, la deuxième **rien**, la
troisième uniquement les grants `fabric-*`, la quatrième daphne.

---

## Ce qui reste après la mise en ligne

1. Créer le premier utilisateur : `manage.py createsuperuser` sur la box, puis
   `create_dev_agent --owner <lui>` pour l'agent du PC.
2. Sur le PC Windows : `FABRIC_SERVER_WS_URL=wss://fabric.foxugly.com/ws/v1/agent/`
   et faire tourner l'agent en service Windows plutôt que dans une fenêtre `cmd`.
3. Sentry frontend (§7 ci-dessus).
