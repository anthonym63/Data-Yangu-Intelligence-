@echo off
REM AI Data Operations Platform - provision, verify, launch (Windows).
REM   launch.bat            -> create venv if missing, install, run tests, start the app
REM   launch.bat --test     -> provision + run tests only
REM   launch.bat --no-test  -> provision + start the app without running tests
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
  echo [launch] creating virtual environment...
  python -m venv venv || exit /b 1
)
echo [launch] installing dependencies...
venv\Scripts\python.exe -m pip install -q -r requirements.txt || exit /b 1

if /i "%~1"=="--no-test" goto run
echo [launch] running test suite...
venv\Scripts\python.exe -m pytest tests/ -q || (echo [launch] tests FAILED - not launching & exit /b 1)
if /i "%~1"=="--test" exit /b 0

:run
if not exist .env (
  echo [launch] NOTE: no .env found. Copy .env.example to .env and set ANTHROPIC_API_KEY
  echo          to enable plan generation. Upload, profiling and cleaning work without it.
)
echo [launch] starting Streamlit...
venv\Scripts\python.exe -m streamlit run app.py
