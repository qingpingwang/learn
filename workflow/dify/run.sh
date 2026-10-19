#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
docker_dir="$root/dify/docker"
profile=dify
export DOCKER_CONTEXT="colima-$profile"

if [ ! -f "$docker_dir/docker-compose.yaml" ]; then
  echo "dify source missing, run: git submodule update --init" >&2
  exit 1
fi

if ! colima status "$profile" >/dev/null 2>&1; then
  echo "starting colima ($profile) ..."
  colima start "$profile" --cpu 4 --memory 8 --disk 60
fi

cd "$docker_dir"
[ -f .env ] || cp .env.example .env
docker compose up -d

echo "waiting for dify ..."
i=0
until curl -fs -o /dev/null http://localhost/console/api/setup; do
  i=$((i + 1))
  if [ "$i" -ge 120 ]; then
    echo "dify not ready, check: docker compose -f $docker_dir/docker-compose.yaml logs" >&2
    exit 1
  fi
  sleep 3
done

echo "dify started: http://localhost"
