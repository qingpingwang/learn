#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
name=${1:?usage: sh run.sh <lesson>}

case $name in
  *[!A-Za-z0-9_-]*)
    echo "bad lesson name: $name" >&2
    exit 1
    ;;
esac

app="$root/src/$name/main.py"
if [ ! -f "$app" ]; then
  echo "no such lesson: $name" >&2
  exit 1
fi

piddir="$root/pid"
logdir="$root/log"
pidfile="$piddir/$name.pid"
mkdir -p "$piddir" "$logdir"

if [ -f "$pidfile" ]; then
  old=$(tr -d '[:space:]' < "$pidfile")
  if kill -0 "$old" 2>/dev/null; then
    echo "$name already running: $old" >&2
    exit 1
  fi
  rm -f "$pidfile"
fi

cd "$root"
nohup "$root/.venv/bin/python" -u "$app" >> "$logdir/$name.log" 2>&1 &
echo $! > "$pidfile"

sleep 0.3
if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "failed to start $name, see log/$name.log" >&2
  rm -f "$pidfile"
  exit 1
fi

echo "$name started: $(cat "$pidfile")"
