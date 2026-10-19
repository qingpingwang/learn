#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
profile=dify
export DOCKER_CONTEXT="colima-$profile"

if colima status "$profile" >/dev/null 2>&1; then
  (cd "$root/dify/docker" && docker compose stop)
  colima stop "$profile"
fi

echo "dify stopped"
