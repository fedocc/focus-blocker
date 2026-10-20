#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
/usr/bin/python3 -I wait_study_idle.py
exec /usr/bin/sudo /usr/bin/python3 -I install.py --anki
