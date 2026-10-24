@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] Python-зависимости...
py -m pip install -q -r requirements.txt || goto :err
echo [2/3] Модели Ollama (первый запуск ~3 ГБ)...
where ollama >nul 2>nul || (echo Ollama не найдена: установите с https://ollama.com/download & goto :err)
if "%LLM_MODEL%"=="" set LLM_MODEL=qwen2.5:7b
ollama pull %LLM_MODEL%
ollama pull bge-m3
echo [3/3] Запуск: http://127.0.0.1:8000  (виджет на портале: http://127.0.0.1:8000/portal-demo.html)
py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof
:err
echo Ошибка запуска.
pause
