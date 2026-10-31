#!/bin/zsh
set -e
series_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/python3 "$series_source_dir/wait_series_idle.py"
/usr/bin/open -g -n '/Applications/Focus Series Guard.app' --args --request-permission
printf '\nВключи Focus Series Guard в «Конфиденциальность и безопасность → Управление устройством и доступ к данным (на старых macOS: Универсальный доступ)».\nЕсли помощник уже включён, просто вернись сюда и нажми Enter.\n'
IFS= read -r series_permission_ready
/usr/bin/python3 "$series_source_dir/wait_series_idle.py"
/usr/bin/osascript "$series_source_dir/install_schedule.applescript"
