@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   JumpCoach 웹앱을 시작합니다...
echo   서버 창이 새로 열립니다. 사용을 마치면 그 창을 닫으세요.
echo.

rem 서버를 별도 창에서 실행
start "JumpCoach server" "%PY%" "%~dp0web_app.py"

rem 서버 부팅을 잠깐 기다린 뒤 브라우저 열기
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:5000

echo   브라우저가 열렸습니다. (안 열리면 주소창에 http://127.0.0.1:5000 입력)
timeout /t 3 /nobreak >nul
