#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
server="$root/server"
venv="$root/.venv"
piddir="$root/pid"
logdir="$root/log"
pidfile="$piddir/room.pid"
host=${HOST:-127.0.0.1}
port=${PORT:-8090}
PATH="$HOME/.local/bin:$PATH"

mkdir -p "$piddir" "$logdir"

if [ -f "$pidfile" ]; then
  old=$(tr -d '[:space:]' < "$pidfile")
  if kill -0 "$old" 2>/dev/null; then
    echo "room already running: $old" >&2
    exit 1
  fi
  rm -f "$pidfile"
fi

if [ ! -x "$venv/bin/python" ]; then
  echo "creating venv ..."
  if command -v uv >/dev/null; then
    uv venv "$venv" --python 3.12
  else
    python3 -m venv "$venv"
  fi
fi
if command -v uv >/dev/null; then
  uv pip install -q --python "$venv/bin/python" -r "$server/requirements.txt"
else
  "$venv/bin/python" -m pip install -q -r "$server/requirements.txt"
fi

cd "$server"
nohup "$venv/bin/python" -m uvicorn server:app --host "$host" --port "$port" \
  --proxy-headers >> "$logdir/room.log" 2>&1 &
echo $! > "$pidfile"

echo "waiting for room ..."
i=0
until curl -fs -o /dev/null "http://127.0.0.1:$port/webrtc/api/config"; do
  if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "failed to start room, see log/room.log" >&2
    rm -f "$pidfile"
    exit 1
  fi
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "room not ready, see log/room.log" >&2
    exit 1
  fi
  sleep 1
done

echo "room started: http://127.0.0.1:$port/webrtc ($(cat "$pidfile"))"
