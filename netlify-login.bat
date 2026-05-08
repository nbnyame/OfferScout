@echo off
echo ================================
echo  Netlify Login
echo ================================
echo.
echo This will open your browser to authenticate with Netlify.
echo.
pause

cd /d "%~dp0"
netlify login

echo.
echo Login complete! You can now run deploy.bat
pause
