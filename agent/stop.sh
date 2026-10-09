#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
piddir="$root/pid"

[ -d "$piddir" ] || exit 0

for pidfile in "$piddir"/*.pid; do
  [ -f "$pidfile" ] || continue
  pid=$(tr -d '[:space:]' < "$pidfile")
  case $pid in
    ''|*[!0-9]*)
      echo "skip bad pid file: $pidfile" >&2
      ;;
    *)
      if kill "$pid" 2>/dev/null; then
        echo "killed $pid"
      else
        echo "not running: $pid"
      fi
      ;;
  esac
  rm -f "$pidfile"
done
