#!/bin/bash
# Versioned source for /usr/local/sbin/fabric-env-fetch.sh (OPERATIONS.md §3.5/§3.10).
#
# Runs as ROOT from the fabric-env-fetch oneshot, so it must never be executed
# from the django-writable tree: root installs it to /usr/local/sbin from the
# committed git blob. This copy is the reference, never the execution target.
#
# Writes /run/fabric/.env (tmpfs) from AWS SSM /fabric/prod.
set -euo pipefail
umask 027

APP=fabric
REGION=eu-west-1
SSM_PATH="/${APP}/prod"
RUN_DIR="/run/${APP}"
ENV_FILE="${RUN_DIR}/.env"

# The instance role (foxugly-fleet-ec2) must win over root's static
# certbot-route53 keys in /root/.aws, which have no SSM access (§3.5).
export AWS_SHARED_CREDENTIALS_FILE=/dev/null
export AWS_CONFIG_FILE=/dev/null
export AWS_REGION="${REGION}"

install -d -m 0750 -o root -g www-data "${RUN_DIR}"

TMP_FILE="$(mktemp "${RUN_DIR}/.env.XXXXXX")"
trap 'rm -f "${TMP_FILE}"' EXIT

aws ssm get-parameters-by-path \
    --region "${REGION}" \
    --path "${SSM_PATH}" \
    --with-decryption \
    --recursive \
    --output json \
  | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
parameters = payload.get("Parameters", [])
if not parameters:
    sys.exit("fabric-env-fetch: no parameters returned for the SSM path")

for parameter in sorted(parameters, key=lambda item: item["Name"]):
    name = parameter["Name"].rsplit("/", 1)[-1]
    value = parameter["Value"]
    if "\n" in value or "\r" in value:
        # systemd EnvironmentFile is line-based: fail loudly rather than write a
        # file that silently truncates a secret.
        sys.exit(f"fabric-env-fetch: {name} contains a newline")
    print(f"{name}={value}")
' > "${TMP_FILE}"

chown django:www-data "${TMP_FILE}"
chmod 0640 "${TMP_FILE}"
mv -f "${TMP_FILE}" "${ENV_FILE}"
trap - EXIT

echo "fabric-env-fetch: wrote ${ENV_FILE} ($(wc -l < "${ENV_FILE}") variables)"
