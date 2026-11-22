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
# The venv must run on $PYTHON (deadsnakes python3.14, root-owned in /usr/bin;
# the system python3 stays 3.12). A missing venv, or one on another version, is
# rebuilt at its final path — never built elsewhere and moved, since
# console-script shebangs (.venv/bin/daphne, used by fabric-asgi) hard-code the
# venv path. The old one is kept as .venv.prev and put back if the rebuild
# fails. fabric-asgi is restarted by ssm-remote.sh after this script;
# fabric-purge (timer oneshot) picks the new venv up on its next run.
PYTHON=/usr/bin/python3.14
PY_WANT="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_HAVE="$("${VENV}/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
if [[ "${PY_HAVE}" != "${PY_WANT}" ]]; then
    echo ">>> Rebuilding the venv: Python ${PY_HAVE} -> ${PY_WANT}..."
    rm -rf "${VENV}.prev"
    if [[ -e "${VENV}" ]]; then mv "${VENV}" "${VENV}.prev"; fi
    if ! { "$PYTHON" -m venv "${VENV}"            && "${VENV}/bin/python" -m pip install --quiet --require-hashes --no-deps -r "${BACKEND_DIR}/requirements.txt"            && "${VENV}/bin/python" -m pip install --quiet --no-deps -e "${BACKEND_DIR}"            && "${VENV}/bin/python" -m pip check; }; then
        echo "ERROR: venv rebuild failed; restoring the previous venv." >&2
        rm -rf "${VENV}"
        if [[ -e "${VENV}.prev" ]]; then mv "${VENV}.prev" "${VENV}"; fi
        exit 1
    fi
fi
"${VENV}/bin/python" -m pip install --quiet --upgrade pip
# backend/requirements.txt is the hash-pinned lock that CI tests (regenerate
# with the uv command in its header). Installing it with --require-hashes means
# production runs exactly those versions, nothing resolved on this host and no
# artifact that differs from what was hashed. The project itself goes in with
# --no-deps; pip check fails the deploy if the lock misses a dependency.
"${VENV}/bin/python" -m pip install --quiet --require-hashes --no-deps -r "${BACKEND_DIR}/requirements.txt"
"${VENV}/bin/python" -m pip install --quiet --no-deps -e "${BACKEND_DIR}"
"${VENV}/bin/python" -m pip check

cd "${BACKEND_DIR}"

echo "== migrate =="
"${VENV}/bin/python" manage.py migrate --noinput

echo "== collectstatic =="
"${VENV}/bin/python" manage.py collectstatic --noinput --clear

echo "== backend deploy done =="
