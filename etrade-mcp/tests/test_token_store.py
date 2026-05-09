"""Tests for token store."""
import os
import pytest
from pathlib import Path
from cryptography.fernet import Fernet
from server.auth.token_store import TokenStore


@pytest.fixture
def temp_token_file(tmp_path):
    """Provide temporary token file path."""
    return str(tmp_path / "test_tokens.enc")


@pytest.fixture
def test_key():
    """Provide consistent encryption key for testing."""
    return Fernet.generate_key().decode()


@pytest.fixture
def token_store(temp_token_file, test_key, monkeypatch):
    """Provide TokenStore instance with test key."""
    monkeypatch.setenv("ETRADE_TOKEN_KEY", test_key)
    return TokenStore(temp_token_file)


def test_token_store_initialization(temp_token_file, test_key, monkeypatch):
    """Test TokenStore initializes correctly."""
    monkeypatch.setenv("ETRADE_TOKEN_KEY", test_key)
    store = TokenStore(temp_token_file)

    assert store.token_file == Path(temp_token_file)
    assert store.key == test_key.encode()


def test_token_store_generates_key_if_not_set(temp_token_file, monkeypatch):
    """Test TokenStore generates key if ETRADE_TOKEN_KEY not set."""
    monkeypatch.delenv("ETRADE_TOKEN_KEY", raising=False)
    store = TokenStore(temp_token_file)

    # Should have generated a key
    assert store.key is not None
    assert len(store.key) > 0


def test_save_tokens(token_store):
    """Test saving tokens."""
    access_token = "test_access_token_12345"
    access_secret = "test_access_secret_67890"

    token_store.save_tokens(access_token, access_secret)

    # File should exist
    assert token_store.token_file.exists()

    # File should contain encrypted data
    encrypted_data = token_store.token_file.read_bytes()
    assert len(encrypted_data) > 0


def test_load_tokens(token_store):
    """Test loading tokens."""
    access_token = "test_access_token_12345"
    access_secret = "test_access_secret_67890"

    # Save tokens first
    token_store.save_tokens(access_token, access_secret)

    # Load tokens
    loaded_tokens = token_store.load_tokens()

    assert loaded_tokens is not None
    assert loaded_tokens["access_token"] == access_token
    assert loaded_tokens["access_token_secret"] == access_secret


def test_load_tokens_file_not_exists(token_store):
    """Test loading tokens when file doesn't exist."""
    result = token_store.load_tokens()
    assert result is None


def test_load_tokens_with_wrong_key(temp_token_file, monkeypatch):
    """Test loading tokens with wrong encryption key fails gracefully."""
    # Create store with first key
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("ETRADE_TOKEN_KEY", key1)
    store1 = TokenStore(temp_token_file)

    # Save tokens
    store1.save_tokens("token", "secret")

    # Try to load with different key
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("ETRADE_TOKEN_KEY", key2)
    store2 = TokenStore(temp_token_file)

    # Should return None (can't decrypt)
    result = store2.load_tokens()
    assert result is None


def test_clear_tokens(token_store):
    """Test clearing tokens."""
    # Save tokens first
    token_store.save_tokens("token", "secret")
    assert token_store.token_file.exists()

    # Clear tokens
    token_store.clear_tokens()

    # File should be deleted
    assert not token_store.token_file.exists()


def test_clear_tokens_when_no_file(token_store):
    """Test clearing tokens when file doesn't exist (should not error)."""
    assert not token_store.token_file.exists()

    # Should not raise error
    token_store.clear_tokens()

    assert not token_store.token_file.exists()


def test_save_and_load_round_trip(token_store):
    """Test complete save and load cycle."""
    test_tokens = [
        ("short_token", "short_secret"),
        ("very_long_access_token_with_lots_of_characters_1234567890",
         "very_long_secret_token_with_lots_of_characters_0987654321"),
        ("token_with_special_chars!@#$%", "secret_with_special_chars^&*()"),
    ]

    for access_token, access_secret in test_tokens:
        # Save
        token_store.save_tokens(access_token, access_secret)

        # Load
        loaded = token_store.load_tokens()

        # Verify
        assert loaded is not None
        assert loaded["access_token"] == access_token
        assert loaded["access_token_secret"] == access_secret

        # Clear for next iteration
        token_store.clear_tokens()
