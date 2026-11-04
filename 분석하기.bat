@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem --- locate Python (known path first, then PATH) ---
set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
  echo.
  echo   [JumpCoach] 제자리멀리뛰기 영상 분석기
  echo   ------------------------------------------------
  echo   분석할 점프 영상 파일을 이 "분석하기.bat" 위로 드래그하세요.
  echo   (Drag a side-view jump video onto this file.)
  echo.
  pause
  exit /b
)

set "VIDEO=%~1"
set "NAME=%~n1"
echo.
echo   분석 중... / Analyzing: %NAME%
echo.
"%PY%" "%~dp0analyze_jump.py" "%VIDEO%" --out "%~dp0out\%NAME%"
if errorlevel 1 (
  echo.
  echo   오류가 발생했습니다. 위 메시지를 확인하세요.
  echo   (Something went wrong - see the message above.)
  pause
  exit /b
)
echo.
echo   완료! 결과 영상과 그래프를 엽니다. / Done - opening results.
start "" "%~dp0out\%NAME%.annotated.mp4"
start "" "%~dp0out\%NAME%.plots.png"
echo.
echo   결과 폴더: %~dp0out
pause
endlocal
