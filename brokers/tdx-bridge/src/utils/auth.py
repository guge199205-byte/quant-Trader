import hmac


def verify_token(expected: str, provided: str) -> bool:
    """常数时间比较 token."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())
