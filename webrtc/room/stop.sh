#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
pidfile="$root/pid/room.pid"

[ -f "$pidfile" ] || { echo "room not running"; exit 0; }

pid=$(tr -d '[:space:]' < "$pidfile")
if kill "$pid" 2>/dev/null; then
  i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do
    i=$((i + 1))
    sleep 1
  done
  echo "killed $pid"
else
  echo "not running: $pid"
fi
rm -f "$pidfile"
