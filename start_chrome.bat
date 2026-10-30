@echo off
REM Opens a separate Chrome window the bot can attach to.
REM First time: log into LinkedIn in this window. It remembers you after that.
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0chrome-profile" https://www.linkedin.com/
