#!/bin/zsh
set -e
series_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/open -g -n '/Applications/Focus Series Guard.app' --args --request-permission
printf '\nВключи Focus Series Guard в «Конфиденциальность и безопасность → Универсальный доступ».\nЗатем вернись сюда и нажми Enter.\n'
IFS= read -r series_permission_ready
/usr/bin/osascript "$series_source_dir/install_schedule.applescript"
