# Fabric — root-side deploy, executed by AWS SSM (AWS-RunShellScript).
#
# ⚠️ Runs as ROOT under /bin/sh (dash) with a minimal env and no $HOME.
# Hence `set -eu` (dash rejects `-o pipefail`) and an explicit HOME.
# See OPERATIONS.md §3.11 "git-blob-install gotchas".
#
# __SHA__ and __BUNDLE__ are substituted by the deploy workflow. Keep this file
# POSIX sh and LF-only (.gitattributes enforces the latter).
set -eu

export HOME=/root

# Force the EC2 instance role over root's static certbot-route53 keys in
# /root/.aws, which would otherwise shadow it — that IAM user has no S3 or SSM
# access, so `aws s3 cp` fails with a bare 403 (OPERATIONS.md §3.5). Every aws
# call in an SSM-run script needs this, exactly like the env-fetch unit does.
export AWS_SHARED_CREDENTIALS_FILE=/dev/null
export AWS_CONFIG_FILE=/dev/null
export AWS_REGION=eu-west-1

APP_DIR=/var/www/django_websites/Fabric
BRANCH=main
BUNDLE=__BUNDLE__
SPA_DIR="$APP_DIR/frontend/dist/fabric-frontend/browser"
VHOST=/etc/nginx/sites-available/fabric.foxugly.com
SHA=__SHA__
# Root's OWN bare mirror (root:root 700): every root-installed blob is read
# here, fetched from GitHub at the exact deployed SHA — never from the
# django-owned checkout, whose .git (refs, config, hooks) django could rewrite
# to make root install or run attacker content (§3.10).
MIRROR=/var/lib/fleet-deploy/Fabric.git

echo "== deploying __SHA__ =="

# Root no longer runs git inside the django-owned repo: drop its old trust.
git config --global --fixed-value --unset-all safe.directory "$APP_DIR" || true

echo "== fetch root mirror =="
install -d -m 700 -o root -g root /var/lib/fleet-deploy
[ -d "$MIRROR" ] || git init --quiet --bare "$MIRROR"
git --git-dir="$MIRROR" fetch --quiet --depth=1 https://github.com/Foxugly/Fabric.git "$SHA"
git --git-dir="$MIRROR" cat-file -e "$SHA^{commit}"

echo "== sync code =="
cd "$APP_DIR"
sudo -u django git fetch --prune origin "$BRANCH"
sudo -u django git reset --hard "origin/$BRANCH"

# Root-loaded artifacts come from the committed git blob in root's mirror,
# never from the django-writable working tree or its .git (§3.10).
echo "== install env-fetch script =="
git --git-dir="$MIRROR" show "$SHA:deploy/fetch-env-from-ssm.sh" > /usr/local/sbin/fabric-env-fetch.sh
chown root:root /usr/local/sbin/fabric-env-fetch.sh
chmod 0755 /usr/local/sbin/fabric-env-fetch.sh

echo "== install systemd units =="
for u in fabric-env-fetch.service fabric-asgi.service fabric-purge.service fabric-purge.timer; do
    git --git-dir="$MIRROR" show "$SHA:deploy/systemd/$u" > "/etc/systemd/system/$u"
done
systemctl daemon-reload

echo "== install nginx vhost =="
if [ -f "$VHOST" ]; then cp -a "$VHOST" "$VHOST.deploybak"; fi
git --git-dir="$MIRROR" show "$SHA:deploy/nginx/fabric.foxugly.com.conf" > "$VHOST"
ln -sfn "$VHOST" /etc/nginx/sites-enabled/fabric.foxugly.com
if nginx -t; then
    systemctl reload nginx
else
    echo "nginx -t failed, rolling the vhost back" >&2
    if [ -f "$VHOST.deploybak" ]; then
        mv -f "$VHOST.deploybak" "$VHOST"
    else
        rm -f "$VHOST" /etc/nginx/sites-enabled/fabric.foxugly.com
    fi
    nginx -t && systemctl reload nginx
    exit 1
fi

echo "== unpack the SPA =="
TMP=$(mktemp -d)
aws s3 cp "$BUNDLE" "$TMP/spa.tar.gz"
sudo -u django mkdir -p "$SPA_DIR"
tar -xzf "$TMP/spa.tar.gz" -C "$SPA_DIR" --no-same-owner
rm -rf "$TMP"

# Frontend Sentry config, substituted into index.html from the SSM-fetched env.
# The fleet normally injects SPA config with an nginx sub_filter, which needs a
# CSP nonce; a single-origin vhost has no sub_filter, so this is done once at
# deploy time instead. Same variable names as §3.8. Placeholders left as-is when
# unset, and the SPA treats a leftover placeholder as "Sentry off".
if [ -r /run/fabric/.env ]; then
    DSN=$(sed -n 's/^SENTRY_FRONTEND_DSN=//p' /run/fabric/.env | head -1)
    ENVN=$(sed -n 's/^SENTRY_FRONTEND_ENV=//p' /run/fabric/.env | head -1)
    if [ -n "$DSN" ]; then
        echo "== inject frontend Sentry config =="
        # `|` as the sed delimiter: a DSN is full of slashes.
        sed -i \
            -e "s|__FABRIC_SENTRY_FRONTEND_DSN__|${DSN}|g" \
            -e "s|__FABRIC_SENTRY_FRONTEND_ENV__|${ENVN:-PROD}|g" \
            -e "s|__FABRIC_SENTRY_FRONTEND_RELEASE__|__SHA__|g" \
            "$SPA_DIR/index.html"
    else
        echo "== frontend Sentry not configured (SENTRY_FRONTEND_DSN unset) =="
    fi
fi

echo "== backend deploy (as django) =="
sudo -u django bash "$APP_DIR/deploy/ssm-deploy.sh"

echo "== restart =="
# enable: the unit was disabled while Fabric was stopped (2026-10-09 → 2026-10-20);
# idempotent, and keeps it up after a reboot.
systemctl enable --quiet fabric-asgi
systemctl restart fabric-asgi
# Daily retention purge (I4): idempotent, and keeps it scheduled after a reboot.
systemctl enable --now fabric-purge.timer

# chown BEFORE chmod: stripping o-rwx first would lock django out of files it
# does not yet own (§3.1).
echo "== normalize perms =="
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

echo "== health =="
sleep 5
curl -fsS -o /dev/null -w 'health %{http_code}\n' https://fabric.foxugly.com/health/
echo "== done =="
