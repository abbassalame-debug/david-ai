@echo off
:: Mode B pre-warm: everything runs on qwen3.5:9b (thinking) -- same model/ctx as Mode A.
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\": \"qwen3.5:9b\", \"keep_alive\": -1, \"options\": {\"num_ctx\": 16384}}" >nul
if not defined DAVID_DATA set "DAVID_DATA=%~dp0data"
if not exist "%DAVID_DATA%" mkdir "%DAVID_DATA%"
echo B> "%DAVID_DATA%\mode.state"
echo Mode B: qwen3.5:9b (deep reasoning) warm.
