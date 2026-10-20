#!/usr/bin/env bash
# Smoke test for the built image: starts it against the fixture recipe repository and checks it.
# Usage: scripts/smoke_image.sh IMAGE
set -euo pipefail

image=${1:?usage: smoke_image.sh IMAGE}
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
volume="cockpit-smoke-$$"
container="cockpit-smoke-$$"

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT

openssl rand -base64 32 > "$work/key"
chmod 644 "$work/key"

docker volume create "$volume" >/dev/null
docker run --rm --user 0 -v "$volume:/data" -v "$root/tests/fixtures/recipe-repo:/fixture:ro" --entrypoint sh "$image" -c \
    'cp -r /fixture /data/repo && git -C /data/repo init -q -b main && git -C /data/repo add -A && git -C /data/repo -c user.name=smoke -c user.email=smoke@example.invalid commit -qm fixture && chown -R 10001:10001 /data'

docker run -d --name "$container" --read-only --tmpfs /tmp -v "$volume:/data" \
    -v "$work/key:/run/secrets/cockpit_master_key:ro" -p 127.0.0.1:18080:8080 "$image" >/dev/null

fail() {
    echo "smoke test failed: $1" >&2
    docker logs "$container" >&2 || true
    exit 1
}

up=0
for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
        up=1
        break
    fi
    sleep 1
done
[ "$up" = 1 ] || fail "/healthz did not answer within 60 s"

status=0
output=$(docker exec "$container" cockpit check 2>&1) || status=$?
echo "$output"
[ "$status" = 0 ] || fail "cockpit check exited with $status"
case "$output" in
    *"fixture: 1 step(s), OK"*) ;;
    *) fail "cockpit check did not report 'fixture: 1 step(s), OK'" ;;
esac

healthy=0
for _ in $(seq 1 60); do
    state=$(docker inspect -f '{{.State.Health.Status}}' "$container")
    if [ "$state" = healthy ]; then
        healthy=1
        break
    fi
    sleep 2
done
[ "$healthy" = 1 ] || fail "the container did not become healthy within 120 s"
echo "smoke test passed"
