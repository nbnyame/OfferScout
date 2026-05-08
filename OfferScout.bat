@echo off
title OfferScout
cd /d "%~dp0"

echo.
echo  ================================
echo   OfferScout - Price Comparison
echo  ================================
echo.

:: Kill any existing OfferScout server on port 5000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5000 ^| findstr LISTENING 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Launch the exe (it auto-opens the browser and auto-shuts down when you close the tab)
echo  Starting OfferScout...
echo  (Close the browser tab to stop the server)
echo.
dist\OfferScout\OfferScout.exe
