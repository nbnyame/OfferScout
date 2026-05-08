@echo off
echo ================================
echo  Deploying OfferScout to Netlify
echo ================================
echo.

cd /d "%~dp0"

echo Checking Netlify authentication...
netlify status
if errorlevel 1 (
    echo.
    echo Please authenticate first by running: netlify login
    pause
    exit /b 1
)

echo.
echo Deploying to production...
netlify deploy --prod

echo.
echo Deployment complete!
pause
