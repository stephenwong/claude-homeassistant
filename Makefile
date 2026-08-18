# Home Assistant Configuration Management Makefile

# Load environment variables from .env file
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

# Configuration
HA_HOST ?= your_homeassistant_host
HA_REMOTE_PATH ?= /config/
LOCAL_CONFIG_PATH ?= config/
BACKUP_DIR ?= backups
UV_RUN ?= uv run
TOOLS_PATH ?= tools

# Frigate configuration
FRIGATE_REMOTE_PATH ?= /addon_configs/ccab4aaf_frigate-fa-beta/
FRIGATE_LOCAL_PATH ?= frigate/

# Colors for output
GREEN = \033[0;32m
YELLOW = \033[1;33m
RED = \033[0;31m
NC = \033[0m # No Color

.PHONY: help pull push validate backup backup-search changelog changelog-all clean setup test status reload format-yaml lint check-env test-ssh

# Default target
help:
	@echo "$(GREEN)Home Assistant Configuration Management$(NC)"
	@echo ""
	@echo "Available commands:"
	@echo "  $(YELLOW)pull$(NC)     - Pull latest config from Home Assistant"
	@echo "  $(YELLOW)push$(NC)     - Push local config to Home Assistant (with validation)"
	@echo "  $(YELLOW)validate$(NC) - Run all validation tests"
	@echo "  $(YELLOW)backup$(NC)   - Create timestamped backup of current config"
	@echo "  $(YELLOW)setup$(NC)    - Set up Python environment and dependencies"
	@echo "  $(YELLOW)test$(NC)     - Run validation tests (alias for validate)"
	@echo "  $(YELLOW)status$(NC)   - Show configuration status and validation summary"
	@echo "  $(YELLOW)reload$(NC)   - Reload Home Assistant configuration (without pushing)"
	@echo "  $(YELLOW)format-yaml$(NC) - Format YAML files (usage: make format-yaml [FILES='file1.yaml file2.yaml'])"
	@echo "  $(YELLOW)check-env$(NC) - Validate environment configuration (.env file)"
	@echo "  $(YELLOW)backup-search$(NC) - Search backup archives for a pattern (usage: make backup-search PATTERN='text' [DAYS=7] [LIMIT=5])"
	@echo "  $(YELLOW)changelog-search$(NC) - Search diff changelogs for a pattern (usage: make changelog-search PATTERN='text' [DAYS=7] [LIMIT=5])"
	@echo "  $(YELLOW)changelog$(NC) - Generate changelog for a backup (usage: make changelog BACKUP='path')"
	@echo "  $(YELLOW)changelog-all$(NC) - Generate changelogs for all backups"
	@echo "  $(YELLOW)lint$(NC)     - Run Python linting and format checks (ruff)"
	@echo "  $(YELLOW)clean$(NC)    - Clean up temporary files and caches"

# Pull configuration from Home Assistant
pull: check-env
	@echo "$(GREEN)Pulling configuration from Home Assistant...$(NC)"
	@rsync -avz --delete --exclude-from=.rsync-excludes-pull $(HA_HOST):$(HA_REMOTE_PATH) $(LOCAL_CONFIG_PATH)
	@echo "$(GREEN)Pulling Frigate configuration...$(NC)"
	@mkdir -p $(FRIGATE_LOCAL_PATH)
	@rsync -avz $(HA_HOST):$(FRIGATE_REMOTE_PATH)config.yml $(FRIGATE_LOCAL_PATH)
	@echo "$(GREEN)Configuration pulled successfully!$(NC)"
	@echo "$(YELLOW)Running validation to ensure integrity...$(NC)"
	@$(MAKE) validate

# Push configuration to Home Assistant (with pre-validation)
push: check-env
	@echo "$(GREEN)Validating configuration before push...$(NC)"
	@$(MAKE) validate
	@echo "$(GREEN)Validation passed! Pushing to Home Assistant...$(NC)"
	@rsync -avz --delete --exclude-from=.rsync-excludes-push $(LOCAL_CONFIG_PATH) $(HA_HOST):$(HA_REMOTE_PATH)
	@if [ -f "$(FRIGATE_LOCAL_PATH)config.yml" ]; then \
		echo "$(GREEN)Pushing Frigate configuration...$(NC)"; \
		rsync -avz $(FRIGATE_LOCAL_PATH)config.yml $(HA_HOST):$(FRIGATE_REMOTE_PATH); \
		echo "$(GREEN)Copying Frigate config to HA config folder for backups...$(NC)"; \
		rsync -avz $(FRIGATE_LOCAL_PATH)config.yml $(HA_HOST):$(HA_REMOTE_PATH)frigate_config.yml; \
	fi
	@echo "$(GREEN)Configuration pushed successfully!$(NC)"
	@echo "$(GREEN)Reloading Home Assistant configuration...$(NC)"
	@$(UV_RUN) python $(TOOLS_PATH)/reload_config.py
	@echo "$(GREEN)Configuration deployment complete!$(NC)"

# Run all validation tests
validate: check-setup
	@echo "$(GREEN)Running Home Assistant configuration validation...$(NC)"
	@$(UV_RUN) python $(TOOLS_PATH)/ha_cli.py validate

# Alias for validate
test: validate

# Create backup of current configuration
backup:
	@echo "$(GREEN)Creating backup of current configuration...$(NC)"
	@mkdir -p $(BACKUP_DIR)
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	backup_name="$(BACKUP_DIR)/ha_config_$$timestamp"; \
	while [ -e "$$backup_name.tar.gz" ]; do \
		sleep 1; \
		timestamp=$$(date +%Y%m%d_%H%M%S); \
		backup_name="$(BACKUP_DIR)/ha_config_$$timestamp"; \
	done; \
	set -- "$(LOCAL_CONFIG_PATH)"; \
	if [ -d "$(FRIGATE_LOCAL_PATH)" ]; then set -- "$$@" "$(FRIGATE_LOCAL_PATH)"; fi; \
	if ! tar -czf "$$backup_name.tar.gz" -- "$$@"; then \
		echo "$(RED)Backup failed; no archive was created$(NC)" >&2; \
		rm -f "$$backup_name.tar.gz"; \
		exit 1; \
	fi; \
	if ! tar -tzf "$$backup_name.tar.gz" >/dev/null; then \
		echo "$(RED)Backup failed integrity verification$(NC)" >&2; \
		rm -f "$$backup_name.tar.gz"; \
		exit 1; \
	fi; \
	if ! chmod 600 "$$backup_name.tar.gz"; then \
		echo "$(RED)Backup failed to apply restrictive permissions$(NC)" >&2; \
		rm -f "$$backup_name.tar.gz"; \
		exit 1; \
	fi; \
	echo "$(GREEN)Backup created: $$backup_name.tar.gz$(NC)"; \
	if $(UV_RUN) python $(TOOLS_PATH)/generate_changelog.py "$$backup_name.tar.gz"; then \
		echo "$(GREEN)Changelog generated$(NC)"; \
	else \
		echo "$(YELLOW)Warning: changelog generation failed$(NC)" >&2; \
	fi

# Search backup archives for a pattern
backup-search:
	@$(UV_RUN) python $(TOOLS_PATH)/search_backups.py $(if $(DAYS),--days $(DAYS),) $(if $(LIMIT),--limit $(LIMIT),) $(if $(CONTEXT),-C $(CONTEXT),) "$(PATTERN)"

# Search diff changelogs for a pattern
changelog-search:
	@$(UV_RUN) python $(TOOLS_PATH)/search_backups.py --changelogs $(if $(DAYS),--days $(DAYS),) $(if $(LIMIT),--limit $(LIMIT),) $(if $(CONTEXT),-C $(CONTEXT),) "$(PATTERN)"

# Generate changelog for a specific backup
changelog:
	@$(UV_RUN) python $(TOOLS_PATH)/generate_changelog.py "$(BACKUP)"

# Generate changelogs for all backups
changelog-all:
	@$(UV_RUN) python $(TOOLS_PATH)/generate_changelog.py --generate-all

# Set up Python environment and dependencies
setup:
	@echo "$(GREEN)Setting up Python environment...$(NC)"
	@uv sync
	@echo "$(GREEN)Setup complete!$(NC)"

# Show configuration status
status: check-setup
	@echo "$(GREEN)Home Assistant Configuration Status$(NC)"
	@echo "=================================="
	@echo "Config directory: $(LOCAL_CONFIG_PATH)"
	@echo "Remote host: $(HA_HOST)"
	@echo ""
	@if [ -f "$(LOCAL_CONFIG_PATH)configuration.yaml" ]; then \
		echo "$(GREEN)✓$(NC) Configuration file found"; \
	else \
		echo "$(RED)✗$(NC) Configuration file missing"; \
	fi
	@if [ -d "$(LOCAL_CONFIG_PATH).storage" ]; then \
		echo "$(GREEN)✓$(NC) Storage directory found"; \
	else \
		echo "$(RED)✗$(NC) Storage directory missing"; \
	fi
	@echo ""
	@echo "$(YELLOW)Entity Summary:$(NC)"
	@$(UV_RUN) python -m tools.validators.references --no-summary 2>/dev/null | grep "Examples:" -A 1 -B 1 | head -20

# Reload Home Assistant configuration via API
reload: check-setup
	@echo "$(GREEN)Reloading Home Assistant configuration...$(NC)"
	@$(UV_RUN) python $(TOOLS_PATH)/reload_config.py

# Format YAML files (specific files or all in config directory)
format-yaml:
	@echo "$(GREEN)Formatting YAML files...$(NC)"
	@if [ -n "$(FILES)" ]; then \
		echo "Formatting specified files: $(FILES)"; \
		for file in $(FILES); do \
			if [ -f "$$file" ]; then \
				echo "Formatting: $$file"; \
				$(UV_RUN) python $(TOOLS_PATH)/format_yaml.py "$$file" || exit 1; \
			else \
				echo "$(YELLOW)Warning: File not found: $$file$(NC)"; \
			fi; \
		done; \
	else \
		echo "Formatting all YAML files in $(LOCAL_CONFIG_PATH) directory..."; \
		for file in $$(find $(LOCAL_CONFIG_PATH) -name "*.yaml" -o -name "*.yml"); do \
			if [ -f "$$file" ]; then \
				echo "Formatting: $$file"; \
				$(UV_RUN) python $(TOOLS_PATH)/format_yaml.py "$$file" || exit 1; \
			fi; \
		done; \
	fi
	@echo "$(GREEN)YAML formatting complete!$(NC)"

# Run Python linting, type checking, and format checks
lint: check-setup
	@echo "$(GREEN)Checking Python formatting...$(NC)"
	@$(UV_RUN) ruff format --check $(TOOLS_PATH)/ tests/
	@echo "$(GREEN)Running Python linter...$(NC)"
	@$(UV_RUN) ruff check $(TOOLS_PATH)/ tests/
	@echo "$(GREEN)Running mypy type checker...$(NC)"
	@$(UV_RUN) mypy $(TOOLS_PATH)/ --exclude "tests/" --exclude "tools/_dev/"
	@echo "$(GREEN)All lint checks passed!$(NC)"

# Auto-fix Python lint and formatting issues
lint-fix: check-setup
	@echo "$(GREEN)Fixing Python formatting...$(NC)"
	@$(UV_RUN) ruff format $(TOOLS_PATH)/ tests/
	@echo "$(GREEN)Fixing Python lint issues...$(NC)"
	@$(UV_RUN) ruff check --fix $(TOOLS_PATH)/ tests/
	@echo "$(GREEN)Lint fixes applied!$(NC)"

# Clean up temporary files
clean:
	@echo "$(GREEN)Cleaning up temporary files...$(NC)"
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.log" -delete 2>/dev/null || true
	@echo "$(GREEN)Cleanup complete!$(NC)"

# Check if setup is complete
check-setup:
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "$(RED)uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh$(NC)"; \
		exit 1; \
	fi
	@if [ ! -f "$(TOOLS_PATH)/ha_cli.py" ]; then \
		echo "$(RED)Validation tools not found.$(NC)"; \
		exit 1; \
	fi

# Check if required environment variables are configured
check-env:
	@if [ "$(HA_HOST)" = "your_homeassistant_host" ] || [ -z "$(HA_HOST)" ]; then \
		echo "$(RED)Error: HA_HOST not configured. Please set it in your .env file.$(NC)"; \
		echo "$(YELLOW)Example: HA_HOST=homeassistant.local$(NC)"; \
		exit 1; \
	fi
	@if [ ! -f ".env" ]; then \
		echo "$(YELLOW)Warning: .env file not found. Copy .env.example to .env and configure your settings.$(NC)"; \
	fi

# Development targets (not shown in help)
.PHONY: pull-storage push-storage validate-yaml validate-references validate-ha

# Pull only storage files (for development)
pull-storage:
	@rsync -avz $(HA_HOST):$(HA_REMOTE_PATH).storage/ $(LOCAL_CONFIG_PATH).storage/

# Individual validation targets
validate-yaml: check-setup
	@$(UV_RUN) python -m tools.validators.yaml

validate-references: check-setup
	@$(UV_RUN) python -m tools.validators.references

validate-ha: check-setup
	@$(UV_RUN) python -m tools.validators.ha_official

# SSH connectivity test
test-ssh:
	@echo "$(GREEN)Testing SSH connection to Home Assistant...$(NC)"
	@ssh -o ConnectTimeout=10 $(HA_HOST) "echo 'Connection successful'" && \
		echo "$(GREEN)✓ SSH connection working$(NC)" || \
		echo "$(RED)✗ SSH connection failed$(NC)"
