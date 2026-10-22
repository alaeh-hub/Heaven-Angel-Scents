#!/usr/bin/env bash
# Runs inside the python:3.12-slim container started by run_tests_docker.ps1:
# copies the repo, installs dependencies, waits for MySQL, runs pytest.
set -euo pipefail

# Debian's MariaDB client verifies TLS and rejects MySQL 8.4's self-signed
# certificate, so turn that off for this throwaway container only.
printf '[client]\nskip-ssl\n' > /root/.my.cnf

apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq default-mysql-client rsync >/dev/null 2>&1

mkdir -p /work
rsync -a --exclude node_modules --exclude .git --exclude .venv --exclude backups \
  --exclude .env --exclude '.env.*' --exclude static/uploads /src/ /work/
cd /work
pip install -q -r requirements.txt

# Give up after ~2 minutes instead of waiting forever if MySQL never answers.
for _ in $(seq 1 60); do
  mysqladmin ping -h "$MYSQL_HOST" -uroot --silent && break
  sleep 2
done
mysqladmin ping -h "$MYSQL_HOST" -uroot --silent || { echo "MySQL did not become reachable" >&2; exit 2; }

python -m pytest -q -p no:cacheprovider "$@"
