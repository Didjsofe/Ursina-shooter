@echo off
chcp 65001 > nul
echo Проверка наличия Python...

:: Проверяем, установлен ли Python в системе
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python не найден в системе!
    echo Открываю Microsoft Store для установки Python 3.12...
    
    :: Открытие страницы Python 3.12 в Microsoft Store
    start ms-windows-store://pdp/?productid=9NCVDNSPBZHV
    
    echo.
    echo 1. Нажмите кнопку «Получить» (Get) в открывшемся окне Microsoft Store[cite: 1].
    echo 2. Дождитесь окончания установки Python.
    echo 3. После завершения установки вернитесь сюда и нажмите любую клавишу.
    pause > nul
    goto CHECK_PYTHON
)

:CHECK_PYTHON
:: Повторная проверка после установки
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python по-прежнему не обнаружен. Убедитесь, что установка завершена.
    pause
    exit
)

echo.
echo Python успешно обнаружен!

:: Создание виртуального окружения venv, если оно еще не создано
if not exist "venv" (
    echo Создание виртуального окружения (venv)...
    python -m venv venv
) else (
    echo Виртуальное окружение уже существует.
)

echo.
echo Активация venv и установка библиотеки ursina...
call venv\Scripts\activate

:: Обновляем pip и ставим ursina внутри venv
python -m pip install --upgrade pip
pip install ursina

echo.
echo Всё готово! Запуск игры...
python main.py
pause