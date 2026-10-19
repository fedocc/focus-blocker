#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
exec /usr/bin/sudo /usr/bin/python3 -I install.py --anki
