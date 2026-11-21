#!/bin/bash
# Fabric backend deploy — runs as the `django` user (OPERATIONS.md §3.11).
#
# Everything root-loaded (systemd units, nginx vhost, /usr/local/sbin scripts) is
# installed by ROOT from the committed git blob in deploy.yml, never from here:
# this script lives in a django-writable tree (§3.10).
#
# The SPA is built in CI and shipped as an S3 bundle (no node on the box), so
# this script only handles the Python side.
set -euo pipefail
umask 027

APP_DIR=/var/www/django_websites/Fabric
BACKEND_DIR="${APP_DIR}/backend"
VENV="${BACKEND_DIR}/.venv"
ENV_FILE=/run/fabric/.env

echo "== load env =="
# Literal key=value parsing, NEVER `source` — values may contain shell-special
# characters that `.` mangles, which once silently emptied a SECRET_KEY (§3.11).
if [[ ! -r "${ENV_FILE}" ]]; then
    echo "missing ${ENV_FILE} — is fabric-env-fetch active?" >&2
    exit 1
fi
while IFS='=' read -r key value; do
    [[ -z "${key}" || "${key}" == \#* ]] && continue
    export "${key}=${value}"
done < "${ENV_FILE}"

echo "== backend deps =="
if [[ ! -x "${VENV}/bin/python" ]]; then
    python3 -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install --quiet --upgrade pip
# pyproject.toml declares ranges, not pins. Without --upgrade, pip keeps any
# installed version that still satisfies a range, so production stayed on
# Django 6.1.2 while CI (fresh install) tested 6.1.3 and its security fixes.
# Eager upgrade makes every deploy install what CI tested: the newest release
# allowed by each range, dependencies of dependencies included.
"${VENV}/bin/python" -m pip install --quiet --upgrade --upgrade-strategy eager -e "${BACKEND_DIR}"

cd "${BACKEND_DIR}"

echo "== migrate =="
"${VENV}/bin/python" manage.py migrate --noinput

echo "== collectstatic =="
"${VENV}/bin/python" manage.py collectstatic --noinput --clear

echo "== backend deploy done =="
