# Fabric — root-side deploy, executed by AWS SSM (AWS-RunShellScript).
#
# ⚠️ Runs as ROOT under /bin/sh (dash) with a minimal env and no $HOME.
# Hence `set -eu` (dash rejects `-o pipefail`), an explicit HOME, and
# git safe.directory. See OPERATIONS.md §3.11 "git-blob-install gotchas".
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

echo "== deploying __SHA__ =="

git config --global --get-all safe.directory 2>/dev/null | grep -qx "$APP_DIR" \
  || git config --global --add safe.directory "$APP_DIR"

echo "== sync code =="
cd "$APP_DIR"
sudo -u django git fetch --prune origin "$BRANCH"
sudo -u django git reset --hard "origin/$BRANCH"

# Root-loaded artifacts come from the committed git blob, never from the
# django-writable working tree (§3.10).
echo "== install env-fetch script =="
git show "origin/$BRANCH:deploy/fetch-env-from-ssm.sh" > /usr/local/sbin/fabric-env-fetch.sh
chown root:root /usr/local/sbin/fabric-env-fetch.sh
chmod 0755 /usr/local/sbin/fabric-env-fetch.sh

echo "== install systemd units =="
git show "origin/$BRANCH:deploy/systemd/fabric-env-fetch.service" > /etc/systemd/system/fabric-env-fetch.service
git show "origin/$BRANCH:deploy/systemd/fabric-asgi.service" > /etc/systemd/system/fabric-asgi.service
systemctl daemon-reload

echo "== install nginx vhost =="
if [ -f "$VHOST" ]; then cp -a "$VHOST" "$VHOST.deploybak"; fi
git show "origin/$BRANCH:deploy/nginx/fabric.foxugly.com.conf" > "$VHOST"
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

echo "== backend deploy (as django) =="
sudo -u django bash "$APP_DIR/deploy/ssm-deploy.sh"

echo "== restart =="
systemctl restart fabric-asgi

# chown BEFORE chmod: stripping o-rwx first would lock django out of files it
# does not yet own (§3.1).
echo "== normalize perms =="
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

echo "== health =="
sleep 5
curl -fsS -o /dev/null -w 'health %{http_code}\n' https://fabric.foxugly.com/health/
echo "== done =="
