"""Module 0, part 2: password and API-token hashing."""
import pytest

from app.utils.security import (
    generate_api_token,
    hash_api_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)


def test_password_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("S3cret-pass")
    assert "S3cret-pass" not in hashed
    assert hashed.startswith("$argon2")
    assert verify_password(hashed, "S3cret-pass") is True


def test_wrong_password_fails():
    assert verify_password(hash_password("right-one"), "wrong-one") is False


def test_same_password_gives_different_hashes():
    # Random salt: two students with the same password do not share a hash.
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize("bad_hash", ["", None, "not-a-hash", "$argon2id$garbage"])
def test_verify_never_crashes_on_corrupt_hash(bad_hash):
    assert verify_password(bad_hash, "anything") is False


@pytest.mark.parametrize("bad_input", ["", None, 12345])
def test_verify_rejects_empty_or_non_string_password(bad_input):
    assert verify_password(hash_password("real"), bad_input) is False


@pytest.mark.parametrize("bad_input", ["", None, 12345])
def test_hash_password_rejects_empty_or_non_string(bad_input):
    with pytest.raises(ValueError):
        hash_password(bad_input)


def test_fresh_hash_does_not_need_rehash_but_garbage_does():
    assert password_needs_rehash(hash_password("x-y-z")) is False
    assert password_needs_rehash("garbage") is True


def test_api_token_is_random_and_hash_is_deterministic():
    token_a, hash_a = generate_api_token()
    token_b, _ = generate_api_token()
    assert token_a != token_b
    assert len(token_a) >= 40
    assert hash_api_token(token_a) == hash_a
    assert token_a not in hash_a
    assert len(hash_a) == 64


@pytest.mark.parametrize("bad_input", ["", None])
def test_hash_api_token_rejects_empty(bad_input):
    with pytest.raises(ValueError):
        hash_api_token(bad_input)
