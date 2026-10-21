@echo off
:: Mode A pre-warm. Models/ctx must match david_router.set_mode("A") and ollama_config.
:: 127.0.0.1, not localhost (a 2 s IPv6 penalty per call on some Windows machines).
:: Pre-warmed set = qwen3.5:9b (5.8 GB). If DAVID_GENERAL_MODEL names a different model (for example a fine-tuned one) it is kept resident too.
:: qwen-ft and qwen2.5-coder:7b are sidelined: never preloaded, and unloaded here if left resident.
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\": \"qwen3.5:9b\", \"keep_alive\": -1, \"options\": {\"num_ctx\": 16384}}" >nul
if defined DAVID_GENERAL_MODEL if /i not "%DAVID_GENERAL_MODEL%"=="qwen3.5:9b" curl -s http://127.0.0.1:11434/api/generate -d "{\"model\": \"%DAVID_GENERAL_MODEL%\", \"keep_alive\": -1, \"options\": {\"num_ctx\": 8192}}" >nul
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\": \"qwen-ft\", \"keep_alive\": 0}" >nul
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\": \"qwen2.5-coder:7b\", \"keep_alive\": 0}" >nul
if not defined DAVID_DATA set "DAVID_DATA=%~dp0data"
if not exist "%DAVID_DATA%" mkdir "%DAVID_DATA%"
echo A> "%DAVID_DATA%\mode.state"
echo Mode A: qwen3.5:9b warm; qwen-ft sidelined (developer runs on qwen3.5:9b).
