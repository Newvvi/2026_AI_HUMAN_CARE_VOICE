@echo off
cd /d "%~dp0"
rem 비밀번호는 실행할 때 입력합니다 (또는 set MQTT_PASSWORD=...)
.\.venv\Scripts\python.exe tools_fake_r2.py --host auto
pause
