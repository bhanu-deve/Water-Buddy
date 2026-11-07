@echo off
rem ============================================================================
rem  Water Assistant launcher.  Double-click this file to start the character.
rem
rem  To change the reminder times: edit the three values below, save, start again.
rem    INTERVAL = minutes between reminders        (45 = every 45 minutes)
rem    HOURS    = when reminders are given         (24-hour clock, START-STOP)
rem                 08:00-22:00 = only from 8 AM to 10 PM
rem                 00:00-00:00 = all day and night
rem    VOLUME   = how loud the voice is, 0-100     (0 = silent)
rem  Close the character with: click it, then Exit.
rem ============================================================================
if not defined INTERVAL set INTERVAL=106
if not defined HOURS set HOURS=08:00-22:00
if not defined VOLUME set VOLUME=100

cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --reminder-interval %INTERVAL% --reminder-hours %HOURS% --volume %VOLUME%
echo.
echo The Water Assistant has stopped. (If this was not on purpose, the message above says why.)
pause
