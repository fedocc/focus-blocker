on run arguments
    if (count of arguments) > 0 then
        set installerPath to item 1 of arguments
    else
        set installerPath to (POSIX path of (path to me)) & "../install.py"
    end if
    set accountName to do shell script "/usr/bin/id -un"
    set repairCommand to "/usr/bin/env SUDO_USER=" & quoted form of accountName & " /usr/bin/python3 -I " & quoted form of installerPath & " --anki --stop-current-study"
    try
        do shell script repairCommand with administrator privileges
        display alert "Фокус исправлен" message "Зависший блок остановлен. Дневной экстренный выход потрачен, история и остальные лимиты сохранены. Перезапусти Anki. Новый блок автоматически не запускается." as informational
    on error errorMessage number errorNumber
        if errorNumber is not -128 then
            display alert "Не удалось завершить ремонт" message errorMessage as critical
        end if
    end try
end run
