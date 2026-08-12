#!/bin/bash

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
SETUP_SCRIPT="$REPO_ROOT/setup-mac.sh"
TEST_ROOT=$(mktemp -d)

cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

source "$SETUP_SCRIPT"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

assert_equal() {
    local expected=$1
    local actual=$2
    local message=$3

    [[ "$actual" == "$expected" ]] || fail "$message (expected '$expected', got '$actual')"
}

assert_file_mode() {
    local expected=$1
    local path=$2
    local message=$3
    local actual

    if stat -c '%a' "$path" >/dev/null 2>&1; then
        actual=$(stat -c '%a' "$path")
    else
        actual=$(stat -f '%Lp' "$path")
    fi
    assert_equal "$expected" "$actual" "$message"
}

test_read_env_value() {
    local env_file="$TEST_ROOT/read.env"

    printf 'HA_URL=http://first.example\nHA_URL=http://second.example\n' > "$env_file"
    assert_equal "http://first.example" "$(read_env_value "$env_file" HA_URL)" \
        "read_env_value returns the first matching value"
    assert_equal "" "$(read_env_value "$env_file" HA_MCP_URL)" \
        "read_env_value returns empty for a missing key"
}

test_prerequisites_preserve_macos_check() {
    local output

    if output=$(OSTYPE=linux check_prerequisites 2>&1); then
        fail "check_prerequisites should reject non-macOS"
    fi
    [[ "$output" == *"This script is for macOS only"* ]] || \
        fail "check_prerequisites should preserve the macOS failure message"
}

test_install_dependencies() {
    local bin_dir="$TEST_ROOT/install-bin"
    local output

    mkdir -p "$bin_dir"
    printf '#!/bin/bash\nexit 0\n' > "$bin_dir/uv"
    chmod +x "$bin_dir/uv"

    output=$(PATH="$bin_dir:$PATH" install_dependencies 2>&1)
    [[ "$output" == *"All Python dependencies verified"* ]] || \
        fail "install_dependencies should verify Python dependencies"
}

test_configure_environment() {
    local work_dir="$TEST_ROOT/environment"
    local bin_dir="$TEST_ROOT/environment-bin"
    local output

    mkdir -p "$work_dir" "$bin_dir"
    printf 'HA_URL=http://existing.example:8123\nHA_MCP_URL=https://mcp.example/private\n' > "$work_dir/.env.example"
    printf 'HA_URL=http://existing.example:8123\nHA_MCP_URL=https://mcp.example/private\n' > "$work_dir/.env"
    printf '#!/bin/bash\nexit 0\n' > "$bin_dir/ping"
    chmod +x "$bin_dir/ping"

    output=$(
        cd "$work_dir"
        PATH="$bin_dir:$PATH" configure_environment <<'EOF'
homeassistant.local

secret-token
EOF
    )

    [[ "$output" == *".env updated with HA_HOST=homeassistant.local and HA_URL=http://existing.example:8123"* ]] || \
        fail "configure_environment should preserve an existing HA_URL default"
    [[ "$(read_env_value "$work_dir/.env" HA_HOST)" == "homeassistant.local" ]] || \
        fail "configure_environment should write HA_HOST"
    [[ "$(read_env_value "$work_dir/.env" HA_TOKEN)" == "secret-token" ]] || \
        fail "configure_environment should write HA_TOKEN"
    assert_file_mode 600 "$work_dir/.env" ".env should have restrictive permissions"
    assert_file_mode 600 "$work_dir/.env.backup" ".env.backup should have restrictive permissions"
    assert_equal "https://mcp.example/private" "$(< "$work_dir/.ha-mcp-url")" \
        "configure_environment should mirror a valid HA_MCP_URL"
    assert_file_mode 600 "$work_dir/.ha-mcp-url" ".ha-mcp-url should have restrictive permissions"

    printf 'HA_URL=http://existing.example:8123\nHA_MCP_URL=not-a-url\n' > "$work_dir/.env"
    (
        cd "$work_dir"
        PATH="$bin_dir:$PATH" configure_environment <<'EOF'
homeassistant.local


EOF
    ) >/dev/null
    [[ ! -e "$work_dir/.ha-mcp-url" ]] || \
        fail "configure_environment should remove an invalid HA_MCP_URL file"
}

test_configure_ssh() {
    local bin_dir="$TEST_ROOT/ssh-bin"

    mkdir -p "$bin_dir"
    printf '#!/bin/bash\nexit 0\n' > "$bin_dir/ssh"
    chmod +x "$bin_dir/ssh"

    PATH="$bin_dir:$PATH" configure_ssh homeassistant.local >/dev/null <<'EOF'
1
EOF
    [[ "$SSH_CONFIGURED" == true ]] || fail "configure_ssh should mark a successful SSH test"

    configure_ssh homeassistant.local >/dev/null <<'EOF'
3
EOF
    [[ "$SSH_CONFIGURED" == false ]] || fail "configure_ssh should mark skipped SSH as unconfigured"
}

test_summary() {
    local output

    HA_HOST=homeassistant.local
    SSH_CONFIGURED=true
    output=$(print_summary)
    [[ "$output" == *"SSH Access: ✅ Configured and tested"* ]] || \
        fail "print_summary should report configured SSH"

    SSH_CONFIGURED=false
    output=$(print_summary)
    [[ "$output" == *"SSH Access: ⚠️  Needs configuration"* ]] || \
        fail "print_summary should report unconfigured SSH"
}

test_main_orchestration() {
    local calls=()

    check_prerequisites() { calls+=(prerequisites); }
    install_dependencies() { calls+=(installation); }
    configure_environment() {
        calls+=(environment)
        HA_HOST=homeassistant.local
    }
    configure_ssh() {
        calls+=(ssh)
        SSH_CONFIGURED=false
    }
    print_summary() { calls+=(summary); }

    main >/dev/null
    assert_equal "prerequisites installation environment ssh summary" "${calls[*]}" \
        "main should orchestrate setup stages in order"
}

test_read_env_value
test_prerequisites_preserve_macos_check
test_install_dependencies
test_configure_environment
test_configure_ssh
test_summary
test_main_orchestration
printf 'PASS: setup-mac.sh isolated shell tests\n'
