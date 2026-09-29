from app.core.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hashing():
    hashed = hash_password("Password123!")
    assert hashed != "Password123!"
    assert verify_password("Password123!", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token():
    token = create_access_token("user-1", "USER")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-1"
    assert payload["role"] == "USER"
