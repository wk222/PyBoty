from __future__ import annotations

import time
from pathlib import Path

import pytest
from core.plugin_sdk.file_lock import FileLockTimeout, acquire_file_lock
from core.plugin_sdk.webhook_guards import WebhookValidationError, verify_hmac_signature
from core.systems.integration.plugin_manifest import PluginManifest, PluginRegistry


def test_file_lock_acquires_and_releases(tmp_path: Path):
    lock_file = tmp_path / "test.lock"

    with acquire_file_lock(lock_file):
        assert lock_file.exists()

    assert not lock_file.exists()


def test_file_lock_timeout(tmp_path: Path):
    lock_file = tmp_path / "test.lock"

    with acquire_file_lock(lock_file):
        with pytest.raises(FileLockTimeout):
            with acquire_file_lock(lock_file, timeout=0.1, retry_interval=0.05):
                pass


def test_webhook_hmac_signature():
    secret = "my-secret"
    payload = b'{"hello": "world"}'
    # Pre-computed sha256 HMAC for the above
    import hashlib
    import hmac

    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    valid_sig = mac.hexdigest()

    assert verify_hmac_signature(payload, valid_sig, secret) is True
    assert verify_hmac_signature(payload, "sha256=" + valid_sig, secret, header_prefix="sha256=") is True
    assert verify_hmac_signature(payload, "wrong-sig", secret) is False


def test_plugin_uninstall_protection():
    registry = PluginRegistry()
    manifest = PluginManifest(
        id="core-plugin",
        name="Core",
        metadata={"protected": True},
    )
    registry.register(manifest)

    with pytest.raises(ValueError, match="is protected and cannot be uninstalled"):
        registry.unregister("core-plugin")
