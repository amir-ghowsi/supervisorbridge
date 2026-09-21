import hashlib


def compute_sha256(data: str | bytes) -> str:
    """
    Computes the SHA-256 hash of exact raw input.
    If input is string, encodes as UTF-8 without normalization or modification.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
