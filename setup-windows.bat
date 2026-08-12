@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Home Assistant Configuration Management - Windows Setup Script
REM This script checks prerequisites and sets up your environment

echo.
echo ========================================================
echo   Home Assistant Configuration Management - Windows Setup
echo ========================================================
echo.

REM Check that the script is being run from the repository root before making
REM environment or dependency changes.
if not exist "Makefile" (
    echo [ERROR] Makefile not found. Run this script from the repository root.
    pause
    exit /b 1
)
if not exist "pyproject.toml" (
    echo [ERROR] pyproject.toml not found. Run this script from the repository root.
    pause
    exit /b 1
)

REM ===============================
REM Phase 1: Check All Prerequisites
REM ===============================
echo Checking prerequisites...
echo.

set MISSING_REQUIRED=0

REM --- Check Python ---
set PYTHON_OK=0
python --version >nul 2>&1
if errorlevel 1 (
    echo [MISSING] Python - REQUIRED
    set MISSING_REQUIRED=1
) else (
    for /f "tokens=2 delims= " %%i in ('python --version 2^>nul') do set PYTHON_VERSION=%%i
    if "!PYTHON_VERSION!"=="" set PYTHON_VERSION=Unknown
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,14,2) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [MISSING] Python !PYTHON_VERSION! found, but Python 3.14.2+ is required
        set MISSING_REQUIRED=1
    ) else (
        echo [OK] Python !PYTHON_VERSION!
        set PYTHON_OK=1
    )
)

REM --- Check Git ---
set GIT_OK=0
where git >nul 2>&1
if errorlevel 1 (
    REM Try common Git installation paths
    if exist "C:\Program Files\Git\cmd\git.exe" (
        set "PATH=%PATH%;C:\Program Files\Git\cmd"
        echo [OK] Git (found in Program Files)
        set GIT_OK=1
    ) else if exist "C:\Program Files (x86)\Git\cmd\git.exe" (
        set "PATH=%PATH%;C:\Program Files (x86)\Git\cmd"
        echo [OK] Git (found in Program Files x86)
        set GIT_OK=1
    ) else (
        echo [MISSING] Git - REQUIRED
        set MISSING_REQUIRED=1
    )
) else (
    echo [OK] Git
    set GIT_OK=1
)

REM Git Bash provides the POSIX tools used by the Makefile when available.
if exist "C:\Program Files\Git\usr\bin" set "PATH=%PATH%;C:\Program Files\Git\usr\bin"
if exist "C:\Program Files (x86)\Git\usr\bin" set "PATH=%PATH%;C:\Program Files (x86)\Git\usr\bin"

REM --- Check Make ---
set MAKE_OK=0
make --version >nul 2>&1
if errorlevel 1 (
    REM Try Git Bash location
    if exist "C:\Program Files\Git\usr\bin\make.exe" (
        set "PATH=%PATH%;C:\Program Files\Git\usr\bin"
        echo [OK] Make (found in Git Bash)
        set MAKE_OK=1
    ) else if exist "C:\Program Files (x86)\Git\usr\bin\make.exe" (
        set "PATH=%PATH%;C:\Program Files (x86)\Git\usr\bin"
        echo [OK] Make (found in Git Bash x86)
        set MAKE_OK=1
    ) else (
        echo [MISSING] Make - REQUIRED
        set MISSING_REQUIRED=1
    )
) else (
    echo [OK] Make
    set MAKE_OK=1
)

REM --- Check rsync ---
set RSYNC_OK=0
where rsync >nul 2>&1
if errorlevel 1 (
    echo [MISSING] rsync - REQUIRED for make pull and make push
    set MISSING_REQUIRED=1
) else (
    echo [OK] rsync
    set RSYNC_OK=1
)

REM --- Check SSH ---
set SSH_OK=0
where ssh >nul 2>&1
if errorlevel 1 (
    echo [MISSING] SSH - REQUIRED for Home Assistant access
    set MISSING_REQUIRED=1
) else (
    echo [OK] SSH
    set SSH_OK=1
)

REM --- Check PowerShell (used for uv installation and secure token input) ---
set POWERSHELL_OK=0
set "POWERSHELL_CMD="
where powershell >nul 2>&1
if errorlevel 1 (
    where pwsh >nul 2>&1
    if errorlevel 1 (
        echo [MISSING] PowerShell - REQUIRED for setup
        set MISSING_REQUIRED=1
    ) else (
        echo [OK] PowerShell 7 (pwsh)
        set "POWERSHELL_CMD=pwsh"
        set POWERSHELL_OK=1
    )
) else (
    echo [OK] PowerShell
    set "POWERSHELL_CMD=powershell"
    set POWERSHELL_OK=1
)

REM --- Check uv (Python package manager) ---
set UV_OK=0
uv --version >nul 2>&1
if errorlevel 1 (
    echo uv not found. Installing uv...
    %POWERSHELL_CMD% -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    uv --version >nul 2>&1
    if errorlevel 1 (
        echo [MISSING] uv - installation failed or uv is not on PATH
        set MISSING_REQUIRED=1
    ) else (
        echo [OK] uv installed
        set UV_OK=1
    )
) else (
    for /f "tokens=2 delims= " %%i in ('uv --version 2^>nul') do set UV_VERSION=%%i
    echo [OK] uv !UV_VERSION!
    set UV_OK=1
)

echo.

REM ===============================
REM Phase 2: Report Missing Dependencies
REM ===============================
if %MISSING_REQUIRED%==1 (
    echo ========================================================
    echo   MISSING REQUIRED DEPENDENCIES
    echo ========================================================
    echo.
    echo The following tools must be installed before continuing:
    echo.
    if %PYTHON_OK%==0 (
        echo   [X] Python
        echo       Download: https://www.python.org/downloads/
        echo       IMPORTANT: Check "Add Python to PATH" during installation
        echo.
    )
    if %GIT_OK%==0 (
        echo   [X] Git
        echo       Download: https://git-scm.com/download/win
        echo       Includes Git Bash which provides Unix-like tools
        echo.
    )
    if %MAKE_OK%==0 (
        echo   [X] Make
        echo       Option 1: Use Git Bash instead of CMD (recommended)
        echo       Option 2: Install Chocolatey, then: choco install make
        echo       Option 3: Install WSL: wsl --install
        echo.
    )
    if %UV_OK%==0 (
        echo   [X] uv
        echo       Install: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        echo       Or: pip install uv
        echo.
    )
    if %RSYNC_OK%==0 (
        echo   [X] rsync
        echo       Install rsync for Windows or use WSL/Git Bash with rsync available
        echo.
    )
    if %SSH_OK%==0 (
        echo   [X] SSH
        echo       Install Git for Windows or enable the Windows OpenSSH client
        echo.
    )
    if %POWERSHELL_OK%==0 (
        echo   [X] PowerShell
        echo       Enable Windows PowerShell or install PowerShell 7
        echo.
    )
    echo ========================================================
    echo.
    echo Please install the missing dependencies and re-run this script.
    echo.
    echo TIP: For the best experience on Windows, use Git Bash instead
    echo      of Command Prompt. Ensure Git Bash or WSL provides make, rsync, and SSH.
    echo.
    pause
    exit /b 1
)

echo All prerequisites found!
echo.

REM ===============================
REM Phase 4: Project Setup
REM ===============================
echo Setting up Python environment...

REM Install dependencies using uv
echo Installing Python dependencies with uv...
uv sync
if errorlevel 1 (
    echo [ERROR] uv sync failed. Fix the dependency error and re-run this script.
    pause
    exit /b 1
)

echo.
echo Verifying Python environment...

REM Verify critical dependencies are importable
uv run python -c "import aiohttp, homeassistant, jsonschema, requests, ruamel.yaml, voluptuous, yaml" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Critical Python dependencies are not importable. Try running: uv sync
    pause
    exit /b 1
) else (
    echo [OK] All Python dependencies verified
)

echo.
echo Checking project setup...

echo [OK] Makefile found

echo.
echo ========================================================
echo   Home Assistant Configuration
echo ========================================================
echo.
echo Let's configure your Home Assistant connection!
echo.

REM Get Home Assistant host
:get_host
set "HA_HOST="
set /p HA_HOST="Enter your Home Assistant hostname or IP address (e.g., homeassistant.local or 192.168.1.100): "
if "%HA_HOST%"=="" (
    echo [ERROR] Hostname/IP cannot be empty
    goto get_host
)

set "HA_URL_DEFAULT=http://%HA_HOST%:8123"
set "HA_URL="
set /p HA_URL="Enter your Home Assistant API URL [!HA_URL_DEFAULT!]: "
if "!HA_URL!"=="" set "HA_URL=!HA_URL_DEFAULT!"

echo.
echo Testing connection to %HA_HOST%...
ping -n 1 "%HA_HOST%" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Cannot reach %HA_HOST% - please verify the address
    set /p continue_setup="Continue anyway? (y/N): "
    if /i not "!continue_setup!"=="y" (
        echo Setup cancelled. Please check your Home Assistant address and try again.
        pause
        exit /b 1
    )
) else (
    echo [OK] Host %HA_HOST% is reachable
)

echo.
echo SSH Configuration
echo ===================
echo.
echo For secure access, this tool uses SSH keys. Do you have SSH access configured?
echo.
echo Options:
echo 1. I already have SSH key access configured
echo 2. I need help setting up SSH keys
echo 3. Skip SSH setup for now (manual configuration later)
echo.
set /p ssh_option="Choose option (1-3): "

if "%ssh_option%"=="1" (
    echo.
    echo Testing SSH connection to %HA_HOST%...
    REM Test SSH connection
    ssh -o ConnectTimeout=5 -o BatchMode=yes "%HA_HOST%" exit >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] SSH connection failed
        echo.
        echo Please check your SSH configuration and try again.
        echo Common issues:
        echo   - SSH keys not added to Home Assistant
        echo   - Incorrect hostname/IP or SSH config alias
        echo   - SSH addon not enabled in Home Assistant
        echo   - Firewall blocking port 22
        set SSH_CONFIGURED=false
    ) else (
        echo [OK] SSH connection successful!
        set SSH_CONFIGURED=true
    )
) else if "%ssh_option%"=="2" (
    echo.
    echo SSH Setup Help
    echo =================
    echo.
    echo To set up SSH access to Home Assistant:
    echo.
    echo 1. Install the 'SSH ^& Web Terminal' add-on in Home Assistant
    echo    Settings -^> Add-ons -^> Add-on Store -^> Search "SSH"
    echo.
    echo 2. Generate an SSH key pair if you don't have one:
    echo    ssh-keygen -t ed25519 -C "your-email@example.com"
    echo.
    echo 3. Copy your public key to Home Assistant:
    echo    - View your key: type %USERPROFILE%\.ssh\id_ed25519.pub
    echo    - Add it to the SSH addon's "authorized_keys" setting
    echo.
    echo 4. Test the connection:
    echo    ssh %HA_HOST%
    echo.
    echo For detailed instructions, visit:
    echo https://github.com/home-assistant/addons/blob/master/ssh/DOCS.md
    echo.
    echo TIP: On Windows, Git Bash or WSL provide better SSH support.
    echo.
    set SSH_CONFIGURED=false
) else if "%ssh_option%"=="3" (
    echo.
    echo [INFO] Skipping SSH setup - you can configure this later
    set SSH_CONFIGURED=false
) else (
    echo Invalid option. Skipping SSH setup.
    set SSH_CONFIGURED=false
)

REM Persist the host in .env; the Makefile loads configuration from .env.
echo.
echo Updating .env configuration...
if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
) else (
    copy /Y ".env" ".env.backup" >nul
)
if errorlevel 1 (
    echo [ERROR] Could not create the .env backup or working file.
    pause
    exit /b 1
)
set "SETUP_HA_HOST=%HA_HOST%"
set "SETUP_HA_URL=%HA_URL%"
set "MCP_URL="
%POWERSHELL_CMD% -NoProfile -Command "$path='.env'; $text=[IO.File]::ReadAllText($path); $secure=Read-Host 'Enter a long-lived access token (leave blank to configure later)' -AsSecureString; $token=''; if ($secure.Length -gt 0) { $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure); try { $token=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) } }; $updates=@{HA_HOST=$env:SETUP_HA_HOST; HA_URL=$env:SETUP_HA_URL}; if ($token) { $updates['HA_TOKEN']=$token }; foreach ($key in $updates.Keys) { $pattern='(?m)^'+[regex]::Escape($key)+'[ \t?]*=.*$'; $line=$key+'='+$updates[$key]; if ($text -match $pattern) { $text=[regex]::Replace($text,$pattern,[System.Text.RegularExpressions.MatchEvaluator]{ param($m) $line }) } else { $text=$text.TrimEnd([char]13,[char]10)+[Environment]::NewLine+$line+[Environment]::NewLine } }; [IO.File]::WriteAllText($path,$text,(New-Object Text.UTF8Encoding($false)))"
if errorlevel 1 (
    echo [ERROR] Could not update .env.
    pause
    exit /b 1
)
echo [OK] .env updated with HA_HOST=%HA_HOST% and HA_URL=%HA_URL%
for /f "tokens=1,* delims==" %%A in ('findstr /b "HA_MCP_URL=" ".env"') do set "MCP_URL=%%B"
if not defined MCP_URL (
    del /q ".ha-mcp-url" >nul 2>&1
) else if /i "!MCP_URL:~0,7!"=="http://" (
    echo !MCP_URL!> ".ha-mcp-url"
) else if /i "!MCP_URL:~0,8!"=="https://" (
    echo !MCP_URL!> ".ha-mcp-url"
) else (
    del /q ".ha-mcp-url" >nul 2>&1
)
echo If you left the token blank, edit .env and set HA_TOKEN before validation or deployment.
echo HA_MCP_URL is optional; OpenCode reads it from .ha-mcp-url when configured.

echo.
echo ========================================================
echo   Setup Complete!
echo ========================================================
echo.
echo Configuration Summary:
echo   - Home Assistant Host: %HA_HOST%
echo   - Configuration: .env updated
if "%SSH_CONFIGURED%"=="true" (
    echo   - SSH Access: [OK] Configured and tested
) else (
    echo   - SSH Access: [!] Needs configuration
)
echo.
echo Next steps:
if "%SSH_CONFIGURED%"=="true" (
    echo   1. Pull your configuration:  make pull
    echo   2. Start your preferred AI assistant, if desired
) else (
    echo   1. Complete SSH setup (see instructions above)
    echo   2. Pull your configuration:  make pull
    echo   3. Start your preferred AI assistant, if desired
)
echo.
echo --------------------------------------------------------
echo TIP: Use Git Bash instead of CMD for best compatibility
echo --------------------------------------------------------
echo.
echo Documentation: README.md
echo Issues: https://github.com/stephenwong/ai-homeassistant/issues
echo.
pause
