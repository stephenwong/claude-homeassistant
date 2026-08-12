#!/bin/bash

# Home Assistant Configuration Management - Mac Setup Script
# This script sets up everything you need to get started

set -Eeuo pipefail

echo "🏠 Home Assistant Configuration Management - Mac Setup"
echo "=================================================="
echo ""

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "❌ This script is for macOS only. Please use setup-windows.bat for Windows."
    exit 1
fi

# Check that the script is being run from the repository root before making
# environment or dependency changes.
if [[ ! -f "Makefile" || ! -f "pyproject.toml" ]]; then
    echo "❌ Makefile or pyproject.toml not found. Run this script from the repository root."
    exit 1
fi

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

echo "🔍 Checking prerequisites..."

# Check if the project-compatible Python is available
if ! command_exists python3; then
    echo "❌ Python 3 is not installed."
    echo "Please install Python from https://www.python.org/downloads/"
    echo "Or use Homebrew: brew install python3"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')

if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 14, 2) else 1)"; then
    echo "❌ Python $PYTHON_VERSION found, but Python 3.14.2+ is required."
    echo "Please upgrade Python from https://www.python.org/downloads/"
    exit 1
fi

echo "✅ Python $PYTHON_VERSION found"

# Check if git is available
if ! command_exists git; then
    echo "❌ Git is not installed."
    echo "Installing Command Line Tools (includes git)..."
    xcode-select --install
    echo "Please run this script again after installation completes."
    exit 1
fi

echo "✅ Git found"

# Check if make is available
if ! command_exists make; then
    echo "❌ Make is not installed."
    echo "Installing Command Line Tools (includes make)..."
    xcode-select --install
    echo "Please run this script again after installation completes."
    exit 1
fi

echo "✅ Make found"

# Check if ssh is available
if ! command_exists ssh; then
    echo "❌ SSH is not available. This is unusual for macOS."
    exit 1
fi

echo "✅ SSH found"

# rsync is required by make pull and make push.
if ! command_exists rsync; then
    echo "❌ rsync is not installed. Install it with: brew install rsync"
    exit 1
fi

echo "✅ rsync found"

echo ""
echo "🐍 Setting up Python environment..."

# Check if uv is available
if ! command_exists uv; then
    echo "Installing uv (Python package manager)..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

if ! command_exists uv; then
    echo "❌ uv installation completed, but the uv command is not on PATH."
    echo "Restart your terminal or add $HOME/.local/bin to PATH, then re-run this script."
    exit 1
fi

echo "✅ uv found"

# Install dependencies
echo "Installing Python dependencies..."
if ! uv sync; then
    echo "❌ uv sync failed. Fix the dependency error and re-run this script."
    exit 1
fi

echo ""
echo "🔍 Verifying Python environment..."

if ! uv run python -c "import aiohttp, homeassistant, jsonschema, requests, ruamel.yaml, voluptuous, yaml"; then
    echo "❌ Critical Python dependencies are not importable. Try running: uv sync"
    exit 1
fi

echo "✅ All Python dependencies verified"

echo ""
echo "🔧 Checking project setup..."

echo "✅ Makefile found"

# Claude Code is optional; the toolkit also works standalone and with other AI
# assistants that support MCP.
if command_exists claude; then
    echo "✅ Claude Code found"
else
    echo "⚠️  Claude Code not found (optional; install it separately if desired)"
fi

echo ""
echo "⚙️  Home Assistant Configuration"
echo "==============================="
echo ""
echo "Let's configure your Home Assistant connection!"
echo ""

# Get Home Assistant host
read -r -p "Enter your Home Assistant hostname or IP address (e.g., homeassistant.local or 192.168.1.100): " HA_HOST
while [ -z "$HA_HOST" ]; do
    echo "❌ Hostname/IP cannot be empty"
    read -r -p "Enter your Home Assistant hostname or IP address: " HA_HOST
done

DEFAULT_HA_URL="http://$HA_HOST:8123"
if [ -f ".env" ]; then
    EXISTING_HA_URL=$(sed -n 's/^HA_URL=//p' .env | head -n 1 || true)
    if [ -n "$EXISTING_HA_URL" ] && [[ "$EXISTING_HA_URL" != *your_homeassistant_host* ]]; then
        DEFAULT_HA_URL="$EXISTING_HA_URL"
    fi
fi
read -r -p "Enter your Home Assistant API URL [$DEFAULT_HA_URL]: " HA_URL
HA_URL=${HA_URL:-$DEFAULT_HA_URL}
read -r -s -p "Enter a long-lived access token (leave blank to configure later): " HA_TOKEN
echo ""

echo ""
echo "📝 Saving Home Assistant connection to .env..."
if [ ! -f ".env" ]; then
    cp .env.example .env
else
    cp .env .env.backup
    chmod 600 .env.backup
fi

HA_HOST_VALUE="$HA_HOST" HA_URL_VALUE="$HA_URL" HA_TOKEN_VALUE="$HA_TOKEN" python3 - ".env" <<'PY'
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
updates = {
    "HA_HOST": os.environ["HA_HOST_VALUE"],
    "HA_URL": os.environ["HA_URL_VALUE"],
}
if os.environ.get("HA_TOKEN_VALUE"):
    updates["HA_TOKEN"] = os.environ["HA_TOKEN_VALUE"]

for key, value in updates.items():
    pattern = rf"^{re.escape(key)}[ \t?]*=.*$"
    replacement = f"{key}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        text = re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += replacement + "\n"
path.write_text(text, encoding="utf-8")
PY
chmod 600 .env

HA_MCP_URL_VALUE=$(sed -n 's/^HA_MCP_URL=//p' .env | head -n 1 || true)
case "$HA_MCP_URL_VALUE" in
    http://*|https://*)
        printf '%s\n' "$HA_MCP_URL_VALUE" > .ha-mcp-url
        chmod 600 .ha-mcp-url
        ;;
    *)
        rm -f .ha-mcp-url
        ;;
esac
echo "✅ .env updated with HA_HOST=$HA_HOST and HA_URL=$HA_URL"

echo ""
echo "Testing connection to $HA_HOST..."
if ping -c 1 "$HA_HOST" >/dev/null 2>&1; then
    echo "✅ Host $HA_HOST is reachable"
else
    echo "⚠️  Warning: Cannot reach $HA_HOST - please verify the address"
    read -r -p "Continue anyway? (y/N): " continue_setup
    if [[ ! "$continue_setup" =~ ^[Yy]$ ]]; then
        echo "Setup cancelled. Please check your Home Assistant address and try again."
        exit 1
    fi
fi

echo ""
echo "🔑 SSH Configuration"
echo "==================="
echo ""
echo "For secure access, this tool uses SSH keys. Do you have SSH access configured?"
echo ""
echo "Options:"
echo "1. I already have SSH key access configured"
echo "2. I need help setting up SSH keys"
echo "3. Skip SSH setup for now (manual configuration later)"
echo ""
read -r -p "Choose option (1-3): " ssh_option

case $ssh_option in
    1)
        # Test SSH connection
        echo ""
        echo "Testing SSH connection to $HA_HOST..."
        if ssh -o ConnectTimeout=5 -o BatchMode=yes "$HA_HOST" exit >/dev/null 2>&1; then
            echo "✅ SSH connection successful!"
            SSH_CONFIGURED=true
        else
            echo "❌ SSH connection failed"
            echo "Please check your SSH configuration and try again"
            echo "Common issues:"
            echo "- SSH keys not added to Home Assistant"
            echo "- Incorrect hostname/IP"
            echo "- SSH addon not enabled in Home Assistant"
            SSH_CONFIGURED=false
        fi
        ;;
    2)
        echo ""
        echo "📚 SSH Setup Help"
        echo "================="
        echo ""
        echo "To set up SSH access to Home Assistant:"
        echo ""
        echo "1. Install the 'SSH & Web Terminal' add-on in Home Assistant"
        echo "2. Generate an SSH key pair if you don't have one:"
        echo "   ssh-keygen -t ed25519 -C \"your-email@example.com\""
        echo ""
        echo "3. Add your public key to the SSH add-on's authorized_keys setting:"
        echo "   cat ~/.ssh/id_ed25519.pub"
        echo ""
        echo "4. Test the connection:"
        echo "   ssh root@$HA_HOST"
        echo ""
        echo "For detailed instructions, visit:"
        echo "https://github.com/home-assistant/addons/blob/master/ssh/DOCS.md"
        echo ""
        SSH_CONFIGURED=false
        ;;
    3)
        echo ""
        echo "⏭️  Skipping SSH setup - you can configure this later"
        SSH_CONFIGURED=false
        ;;
    *)
        echo "Invalid option. Skipping SSH setup."
        SSH_CONFIGURED=false
        ;;
esac

# The Makefile reads HA_HOST from .env; do not rewrite the tracked Makefile.
echo ""
echo "If you left the token blank, edit .env and set HA_TOKEN before validation or deployment."
echo "HA_MCP_URL is optional and only needed for AI assistant integration."

echo ""
echo "🎉 Setup Complete!"
echo "=================="
echo ""
echo "Configuration Summary:"
echo "- Home Assistant Host: $HA_HOST"
echo "- Configuration: .env updated"
if [ "$SSH_CONFIGURED" = true ]; then
    echo "- SSH Access: ✅ Configured and tested"
else
    echo "- SSH Access: ⚠️  Needs configuration"
fi
echo ""
echo "Next steps:"
if [ "$SSH_CONFIGURED" = true ]; then
    echo "1. Pull your actual configuration:"
    echo "   make pull"
    echo ""
    echo "2. Start using the tools or your preferred AI assistant!"
else
    echo "1. Complete SSH setup (see instructions above)"
    echo "2. Pull your actual configuration: make pull"
    echo "3. Start using the tools or your preferred AI assistant!"
fi
echo ""
echo "For detailed instructions, see the README.md file."
echo ""
echo "Need help? Check the troubleshooting section in README.md"
echo "or create an issue at: https://github.com/stephenwong/ai-homeassistant/issues"
