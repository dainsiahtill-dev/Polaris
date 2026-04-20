@echo off
REM Build guard script for Windows - Check for circular chunk warnings in build output

echo Running npm build and checking for circular chunk warnings...

REM Run build and capture output to temp file
npm run build 2>&1 > %TEMP%\build_output.txt

REM Check for circular chunk warnings
findstr /C:"Circular chunk" %TEMP%\build_output.txt >nul
if %errorlevel% equ 0 (
    echo ERROR: Circular chunk dependencies detected in build!
    findstr /C:"Circular chunk" %TEMP%\build_output.txt
    exit /b 1
)

REM Check for TypeScript errors
findstr /C:"error TS" %TEMP%\build_output.txt >nul
if %errorlevel% equ 0 (
    echo ERROR: TypeScript errors detected in build!
    exit /b 1
)

echo Build check passed - no circular chunk warnings found
exit /b 0
