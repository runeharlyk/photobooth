"""Names URLs and request failures for the log without the secrets they may carry."""

import urllib.error
import urllib.parse


def scheme_and_host(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme and parts.hostname:
        return f"{parts.scheme}://{parts.hostname}"
    return "a URL without a scheme and host"


def describe_failure(error: Exception) -> str:
    """Names a request failure without its message, which may quote the URL."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        return type(error.reason).__name__
    return type(error).__name__
