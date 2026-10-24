@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Публичный доступ: сервер помощника + Cloudflare Tunnel. Ссылка меняется при каждом запуске.
if "%OLLAMA_MODELS%"=="" set OLLAMA_MODELS=D:\ollama-models
set CLOUDFLARED="C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not exist %CLOUDFLARED% (echo cloudflared не найден: winget install Cloudflare.cloudflared & pause & exit /b 1)

echo [1/3] Ollama...
curl -s http://127.0.0.1:11434/api/version >nul 2>nul || start "ollama" /min "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve

echo [2/3] Сервер помощника на 127.0.0.1:8000...
start "dou-assistant" /min py -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo [3/3] Туннель. Публичная ссылка появится ниже в строке вида https://....trycloudflare.com
echo Пароль администратора: kb\admin_password.txt. Не закрывайте это окно и не отправляйте ноутбук в сон.
%CLOUDFLARED% tunnel --no-autoupdate --url http://127.0.0.1:8000
