#!/usr/bin/env python3
"""Home Assistant API Diagnostic Tool.

Comprehensive testing of various API endpoints and entity operations.
Combines functionality from multiple diagnostic scripts.
"""

import json
import os
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit

import requests

from tools.common import DEFAULT_HA_URL, get_env_int, load_env_file, validate_ha_url


class DiagnosticConfig(TypedDict):
    """Runtime configuration used by the diagnostic probes."""

    ha_url: str
    token: str
    request_timeout: int


def get_config() -> DiagnosticConfig:
    """Load configuration from environment."""
    load_env_file()
    request_timeout, timeout_warning = get_env_int("HA_REQUEST_TIMEOUT", 10)
    if timeout_warning:
        print(f"⚠️  {timeout_warning}")
    return {
        "ha_url": os.getenv("HA_URL", DEFAULT_HA_URL),
        "token": os.getenv("HA_TOKEN", ""),
        "request_timeout": request_timeout,
    }


def _request(
    ha_url: str,
    token: str,
    endpoint: str,
    *,
    method: str = "GET",
    request_timeout: int = 10,
    payload: object | None = None,
) -> requests.Response:
    """Send a diagnostic request with the shared authentication settings."""
    headers = {"Authorization": f"Bearer {token}"}
    if method == "POST":
        headers["Content-Type"] = "application/json"

    parsed = urlsplit(ha_url)
    url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{parsed.path.rstrip('/')}/{endpoint.lstrip('/')}",
            "",
            "",
        )
    )
    if payload is None:
        return requests.request(method, url, headers=headers, timeout=request_timeout)
    return requests.request(
        method, url, headers=headers, timeout=request_timeout, json=payload
    )


def _safe_json_response(response, error_prefix: str):
    """Parse JSON response, printing a helpful message on decode failures."""
    try:
        return response.json()
    except ValueError:
        preview = response.text[:100] if response.text else "<empty response>"
        print(f"{error_prefix} Invalid JSON response: {preview}")
        return None


def test_api_connection(ha_url, token, request_timeout: int = 10):
    """Test basic API connection."""
    print("🔗 Testing API Connection...")
    try:
        response = _request(ha_url, token, "/api/", request_timeout=request_timeout)

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            data = _safe_json_response(response, "   ❌")
            if data is None:
                return False
            print(f"   Message: {data.get('message', 'No message')}")
            return True
        else:
            print(f"   Error: {response.text}")
            return False
    except requests.RequestException as e:
        print(f"   Exception: {e}")
        return False


def test_api_endpoints(ha_url, token, request_timeout: int = 10):
    """Test various API endpoints to find entity registry access."""
    print("\n🔍 Testing Various API Endpoints...")

    endpoints_to_test = [
        ("/api/config/entity_registry", "Entity Registry"),
        ("/api/config/entity_registry/list", "Entity Registry List"),
        ("/api/states", "Entity States"),
        ("/api/config", "Configuration"),
        ("/api/config/core", "Core Configuration"),
        ("/api/hassio/supervisor/api/config", "Supervisor Config"),
        ("/api/template", "Template API"),
    ]

    successful_endpoints = []

    for endpoint, description in endpoints_to_test:
        try:
            print(f"\n   Testing: {endpoint} ({description})")
            response = _request(
                ha_url, token, endpoint, request_timeout=request_timeout
            )
            print(f"   Status: {response.status_code}")

            if response.status_code == 200:
                successful_endpoints.append(endpoint)
                data = _safe_json_response(response, "   ✅")
                if data is None:
                    print(f"   ✅ Non-JSON response ({len(response.text)} chars)")
                elif isinstance(data, list):
                    print(f"   ✅ List with {len(data)} items")
                    if len(data) > 0:
                        print(f"      Sample type: {type(data[0])}")
                elif isinstance(data, dict):
                    keys = list(data.keys())[:5]
                    print(f"   ✅ Dict with keys: {keys}")
                else:
                    print(f"   ✅ {type(data)}")
            else:
                print(f"   ❌ {response.text[:100]}")

        except requests.RequestException as e:
            print(f"   ❌ Exception: {e}")

    return successful_endpoints


def test_entity_registry_read(ha_url, token, request_timeout: int = 10):
    """Test reading entity registry."""
    print("\n📋 Testing Entity Registry Read Access...")
    try:
        response = _request(
            ha_url,
            token,
            "/api/config/entity_registry",
            request_timeout=request_timeout,
        )

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            data = _safe_json_response(response, "   ❌")
            if not isinstance(data, list):
                return []
            print(f"   ✅ Found {len(data)} entities")

            # Sample first 3 entities for inspection
            sample_entities = data[:3]
            for entity in sample_entities:
                entity_id = entity.get("entity_id")
                print(f"   ✅ Sample: {entity_id}")
                print(f"      Platform: {entity.get('platform')}")
                print(f"      Device ID: {entity.get('device_id')}")
                print(f"      Unique ID: {entity.get('unique_id')}")

            return sample_entities
        else:
            print(f"   ❌ Error: {response.text}")
            return []
    except requests.RequestException as e:
        print(f"   ❌ Exception: {e}")
        return []


def test_states_endpoint(ha_url, token, request_timeout: int = 10):
    """Test the /api/states endpoint to see entity data."""
    print("\n📊 Testing States Endpoint for Entity Info...")
    try:
        response = _request(
            ha_url, token, "/api/states", request_timeout=request_timeout
        )

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            states = _safe_json_response(response, "   ❌")
            if not isinstance(states, list):
                return False
            print(f"   ✅ Found {len(states)} states")

            # Sample first 3 entities for inspection
            for state in states[:3]:
                entity_id = state.get("entity_id")
                print(f"   ✅ Sample: {entity_id}")
                attrs = list(state.get("attributes", {}).keys())[:5]
                print(f"      Attributes: {attrs}")

            return len(states) > 0
        else:
            print(f"   ❌ Error: {response.text}")
            return False
    except requests.RequestException as e:
        print(f"   ❌ Exception: {e}")
        return False


def test_entity_rename(ha_url, token, entity_data_list, request_timeout: int = 10):
    """Test renaming a single entity using multiple methods."""
    print("\n🔄 Testing Entity Rename Methods...")

    if not entity_data_list:
        print("   ❌ No entity data to test with")
        return False

    entity_data = entity_data_list[0]  # Use first discovered entity
    old_id = entity_data.get("entity_id", "unknown")
    domain = old_id.split(".")[0] if "." in old_id else "entity"
    new_id = f"{domain}.rename_test_temp"

    print(f"   Testing rename: {old_id} → {new_id}")

    # Method 1: Direct entity registry update
    try:
        print("\n   Method 1: Direct registry update...")
        data = {"new_entity_id": new_id}
        response = _request(
            ha_url,
            token,
            f"/api/config/entity_registry/{old_id}",
            method="POST",
            request_timeout=request_timeout,
            payload=data,
        )

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Method 1 successful!")
            return True
        else:
            print(f"   ❌ Method 1 failed: {response.text}")

    except requests.RequestException as e:
        print(f"   ❌ Method 1 exception: {e}")

    # Method 2: Update endpoint
    try:
        print("\n   Method 2: Update endpoint...")
        response = _request(
            ha_url,
            token,
            "/api/config/entity_registry/update",
            method="POST",
            request_timeout=request_timeout,
            payload={"entity_id": old_id, "new_entity_id": new_id},
        )

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Method 2 successful!")
            return True
        else:
            print(f"   ❌ Method 2 failed: {response.text}")

    except requests.RequestException as e:
        print(f"   ❌ Method 2 exception: {e}")

    return False


def test_service_call_method(
    ha_url, token, entity_data_list, request_timeout: int = 10
):
    """Test if we can rename via service calls."""
    print("\n🔧 Testing Service Call Method...")

    # Use first discovered entity, or skip if none available
    if not entity_data_list:
        print("   ❌ No entity data to test with")
        return

    target_entity = entity_data_list[0].get("entity_id", "unknown")

    # Test calling homeassistant.update_entity service
    try:
        service_data = {
            "entity_id": target_entity,
            # This changes friendly name, not entity_id
            "name": "Rename Test Temp",
        }

        response = _request(
            ha_url,
            token,
            "/api/services/homeassistant/update_entity",
            method="POST",
            request_timeout=request_timeout,
            payload=service_data,
        )

        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            print("   ✅ Service call successful (friendly name only)")
            print("   Note: This only changes display name, not entity_id")
        else:
            print(f"   ❌ Service call failed: {response.text}")

    except requests.RequestException as e:
        print(f"   ❌ Exception: {e}")


def show_websocket_info(ha_url: str):
    """Show information about WebSocket method."""
    parsed = urlsplit(ha_url)
    websocket_scheme = "wss" if parsed.scheme == "https" else "ws"
    websocket_path = f"{parsed.path.rstrip('/')}/api/websocket"
    websocket_url = urlunsplit(
        (websocket_scheme, parsed.netloc, websocket_path, "", "")
    )

    print("\n🌐 WebSocket API Information...")
    print("   Entity registry operations likely require WebSocket API:")
    print(f"   • WebSocket URL: {websocket_url}")
    print("   • Auth: Send auth message with Bearer token")
    print("   • List entities: {'type': 'config/entity_registry/list'}")
    print(
        "   • Update entity: {'type': 'config/entity_registry/update', "
        "'entity_id': '...', 'new_entity_id': '...'}"
    )

    websocket_example = {
        "id": 1,
        "type": "config/entity_registry/update",
        "entity_id": "<old_entity_id>",
        "new_entity_id": "<new_entity_id>",
    }

    print("\n   Example WebSocket command:")
    print(f"   {json.dumps(websocket_example, indent=2)}")


def main():
    """Run main diagnostic function."""
    print("🏠 Home Assistant API Diagnostic Tool")
    print("=" * 60)

    config = get_config()
    ha_url = config["ha_url"]
    token = config["token"]
    request_timeout = config["request_timeout"]

    url_error = validate_ha_url(ha_url)
    if url_error:
        print(f"❌ {url_error}")
        return

    if not token:
        print("❌ No HA_TOKEN found in .env file!")
        print("   Create a .env file with: HA_TOKEN=your_long_lived_access_token")
        return

    print(f"🔗 Testing connection to: {ha_url}")

    # Test 1: Basic connection
    if not test_api_connection(ha_url, token, request_timeout):
        print("❌ Basic connection failed - stopping tests")
        return

    # Test 2: Explore available endpoints
    successful_endpoints = test_api_endpoints(ha_url, token, request_timeout)

    # Test 3: Entity registry read
    entity_data = test_entity_registry_read(ha_url, token, request_timeout)

    # Test 4: States endpoint
    states_work = test_states_endpoint(ha_url, token, request_timeout)

    # Test 5: Entity rename attempts
    test_entity_rename(ha_url, token, entity_data, request_timeout)

    # Test 6: Service call method
    test_service_call_method(ha_url, token, entity_data, request_timeout)

    # Test 7: WebSocket method info
    show_websocket_info(ha_url)

    # Summary
    print("\n" + "=" * 60)
    print("🎯 DIAGNOSTIC SUMMARY")
    print("=" * 60)
    print(f"✅ Working endpoints: {len(successful_endpoints)}")
    registry_access = "Yes" if entity_data else "No (likely WebSocket only)"
    print(f"✅ Entity registry access: {registry_access}")
    states_status = "Yes" if states_work else "No"
    print(f"✅ States endpoint: {states_status}")
    print("✅ Entity renaming: Requires WebSocket API or UI")
    print("\n📝 RECOMMENDATIONS:")
    print("   1. Use WebSocket API for entity registry operations")
    print("   2. REST API works for states but not entity management")
    print("   3. Service calls only change friendly names, not entity IDs")
    print("   4. Manual UI renaming may be most reliable option")


if __name__ == "__main__":
    main()
