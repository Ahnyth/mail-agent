"""TLS client context with mandatory certificate and hostname verification."""

import ssl

MINIMUM_TLS_VERSION = ssl.TLSVersion.TLSv1_2
"""TLS 1.0 and 1.1 are retired (RFC 8996); only TLS 1.2 and newer are accepted."""


class InsecureTlsContextError(ValueError):
    """A TLS context does not verify the server; it must never be used."""


def build_ssl_context(*, cafile: str | None = None) -> ssl.SSLContext:
    """Creates the client context backed by the operating system trust store.

    ``cafile`` replaces the system store and exists for tests only.
    """
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    context.minimum_version = MINIMUM_TLS_VERSION
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    assert_secure_context(context)
    return context


def assert_secure_context(context: ssl.SSLContext) -> None:
    """Rejects any context that does not check certificate, hostname and protocol version.

    Raises:
        InsecureTlsContextError: One of the three checks is switched off or too weak.
    """
    if context.verify_mode != ssl.CERT_REQUIRED:
        raise InsecureTlsContextError("tls_verification_disabled")
    if not context.check_hostname:
        raise InsecureTlsContextError("tls_hostname_check_disabled")
    if context.minimum_version < MINIMUM_TLS_VERSION:
        raise InsecureTlsContextError("tls_minimum_version_too_low")
