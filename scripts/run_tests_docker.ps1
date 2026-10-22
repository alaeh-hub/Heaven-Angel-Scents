# Runs the test suite against a throwaway MySQL 8.4 container. Usage:
#   powershell -File scripts\run_tests_docker.ps1            # whole suite
#   powershell -File scripts\run_tests_docker.ps1 tests\test_csp.py
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$suffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
$mysqlContainer = "ha-test-mysql-$suffix"
$network = "ha-test-$suffix"
$code = 1

docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker is unavailable or its daemon is not running." }

try {
  docker network create $network | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not create the temporary Docker network." }
  docker run -d --name $mysqlContainer --network $network -e MYSQL_ALLOW_EMPTY_PASSWORD=yes mysql:8.4 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not start the temporary MySQL container." }

  # Strip Windows line endings; pass test selectors as positional args, not shell code.
  $innerCommand = "tr -d '\r' < /src/scripts/docker_test_inner.sh > /tmp/inner.sh && exec bash /tmp/inner.sh"
  docker run --rm --network $network -v "${root}:/src:ro" `
    -e MYSQL_HOST=$mysqlContainer -e MYSQL_USER=root -e MYSQL_PASSWORD= -e PYTHONDONTWRITEBYTECODE=1 `
    python:3.12-slim bash -c $innerCommand test-runner @args
  $code = $LASTEXITCODE
}
finally {
  docker rm -f $mysqlContainer 2>$null | Out-Null
  docker network rm $network 2>$null | Out-Null
}

exit $code
