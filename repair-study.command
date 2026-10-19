#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec /usr/bin/osascript ./repair-study.applescript "$PWD/install.py"
