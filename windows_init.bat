@echo off
setlocal EnableDelayedExpansion

set "MAX_QDB_RETRIES=60"
set "QDB_RETRY_SECONDS=2"

echo Starting Docker engine...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

:check_engine
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker engine is not ready yet. Retrying in 5 seconds...
    timeout /t 5 /nobreak >nul
    goto check_engine
)

echo Docker engine is ready.

echo Starting QuestDB container...
docker start questdb

echo Waiting for QuestDB web UI on localhost:9000 or localhost:9090...
set /a qdb_attempt=0

:check_questdb
set /a qdb_attempt+=1

rem Check web UI root on 9000.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:9000/' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo QuestDB web UI is ready on port 9000.
    goto start_stream
)

rem Fallback check: web UI root on 9090 (if mapped there in your setup).
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:9090/' -TimeoutSec 2; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo QuestDB web UI is ready on port 9090.
    goto start_stream
)

if !qdb_attempt! geq %MAX_QDB_RETRIES% (
    echo ERROR: QuestDB did not become ready after !qdb_attempt! attempts.
    exit /b 1
)

echo QuestDB not ready yet. Attempt !qdb_attempt!/%MAX_QDB_RETRIES%. Retrying in %QDB_RETRY_SECONDS% seconds...
timeout /t %QDB_RETRY_SECONDS% /nobreak >nul
goto check_questdb

:start_stream
echo Initializing streaming script...
cd /d C:\Users\testop0\Documents\gse2.0
call C:\Users\testop0\Documents\gse2.0\.venv\Scripts\activate.bat
python "C:\Users\testop0\Documents\gse2.0\nidaq_client\nidaq_quest_stream.py"
