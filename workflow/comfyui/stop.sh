#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
pidfile="$root/pid/comfyui.pid"

[ -f "$pidfile" ] || { echo "comfyui not running"; exit 0; }

pid=$(tr -d '[:space:]' < "$pidfile")
if kill "$pid" 2>/dev/null; then
  echo "killed $pid"
else
  echo "not running: $pid"
fi
rm -f "$pidfile"
