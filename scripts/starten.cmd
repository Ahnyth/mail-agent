@echo off
rem MailAgent: Browser oeffnen. Laeuft der Agent noch nicht, wird er unsichtbar im Hintergrund
rem gestartet (wie beim Autostart nach der Windows-Anmeldung). Meldungen stehen in daten\agent.log.
cd /d "%~dp0.."
call :laeuft && goto browser
if not exist ".venv\Scripts\pythonw.exe" goto sichtbar
start "" ".venv\Scripts\pythonw.exe" -m mail_agent.background
rem Hoechstens 30 Versuche im Sekundentakt, dann Fehlermeldung (jede Wartezeit hat eine Obergrenze).
for /l %%i in (1,1,30) do (
  ping -n 2 127.0.0.1 >nul
  call :laeuft && goto browser
)
echo Der MailAgent ist nicht gestartet. Letzte Meldungen aus daten\agent.log:
powershell -NoProfile -Command "Get-Content -Tail 15 -Encoding UTF8 'daten\agent.log'"
pause
exit /b 1

:sichtbar
rem Noch nicht eingerichtet (kein .venv): sichtbar starten, Beenden mit Strg+C.
start "" /min cmd /c "ping -n 6 127.0.0.1 >nul & start http://127.0.0.1:8765"
uv run mail-agent web
pause
exit /b 0

:browser
start "" http://127.0.0.1:8765
exit /b 0

:laeuft
netstat -ano -p TCP | findstr /c:"127.0.0.1:8765 " | findstr /c:"0.0.0.0:0" >nul
exit /b %errorlevel%
