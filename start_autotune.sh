#!/usr/bin/env bash
# Deprecated alias — use ./start.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start.sh" "$@"
