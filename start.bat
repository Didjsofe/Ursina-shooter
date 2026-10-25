@echo off
chcp 65001 > nul

echo [1/4] Поиск интерпретатора Python в системе...

set PY_CMD=
python --version >nul 2>&1 && set PY_CMD=python
if not defined PY_CMD (
    py --version >nul 2>&1 && set PY_CMD=py
)
if not defined PY_CMD (
    python3 --version >nul 2>&1 && set PY_CMD=python3
)

if not defined PY_CMD (
    echo [ОШИБКА] Python не найден! Убедитесь, что Python установлен.
    goto AUTO_CLOSE
)

echo Обнаружен Python: %PY_CMD%

echo.
echo [2/4] Проверка виртуального окружения (venv)...
if not exist "venv" (
    echo Папка venv не найдена. Создание нового виртуального окружения...
    %PY_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo [ОШИБКА] Не удалось создать venv.
        goto AUTO_CLOSE
    )
) else (
    echo Виртуальное окружение venv найдено.
)

echo.
echo [3/4] Активация venv и проверка библиотеки Ursina...
call venv\Scripts\activate

:: Проверяем, установлен ли модуль ursina внутри venv
python -c "import ursina" >nul 2>&1
if %errorlevel% neq 0 (
    echo Ursina не найдена в venv. Установка библиотеки...
    python -m pip install --upgrade pip
    pip install ursina
) else (
    echo Библиотека Ursina уже установлена.
)

set "CurrentDir=%~dp0"
set "TargetFile=%CurrentDir%shooter.exe"

:: Получаем актуальный путь к рабочему столу через PowerShell (учитывает OneDrive)
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DesktopPath=%%i"

set "ShortcutPath=%DesktopPath%\shooter.lnk"

:: Проверяем, существует ли ярлык
if exist "%ShortcutPath%" (
    echo Ярлык уже существует на рабочем столе.
) else (
    :: Создаем ярлык, если его нет
    powershell -NoProfile -Command "$WScriptShell = New-Object -ComObject WScript.Shell; $Shortcut = $WScriptShell.CreateShortcut('%ShortcutPath%'); $Shortcut.TargetPath = '%TargetFile%'; $Shortcut.WorkingDirectory = '%CurrentDir%'; $Shortcut.Save()"

    if %errorlevel% equ 0 (
        echo Ярлык успешно создан на рабочем столе!
    ) else (
        echo Ошибка при создании ярлыка.
    )
)


echo.
echo [4/4] Запуск client.py...
python client.py

:AUTO_CLOSE
echo.
echo Работа игры завершена. Окно закроется через 5 секунд, если вы не нажмете любую клавишу...
timeout /t 5 /nobreak >nul