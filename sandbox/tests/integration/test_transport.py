# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_substrate.transport import ProcessTransport, ProcessTransportError


@contextmanager
def response_server(status, body, headers=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append({"actor": self.headers.get("ate-target-actor"), "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("status,body,headers", [
    (503, b"unavailable", None),
    (200, b"not-json", None),
    (200, b'{"stdout":"", "stderr":""}', None),
    (200, b'{"stdout":"", "stderr":"", "exitCode":true}', None),
    (200, b'{"stdout":"", "stderr":"", "exitCode":0, "error":"failed"}', None),
    (307, b"", {"Location": "http://127.0.0.1:1/process"}),
])
async def test_failed_execution_is_never_replayed(status, body, headers):
    with response_server(status, body, headers) as (url, requests):
        with pytest.raises(ProcessTransportError):
            await ProcessTransport(url).execute("space/actor", ["true"], cwd="/workspace")
        assert len(requests) == 1
        assert requests[0]["actor"] == "space/actor"


async def test_oversized_response_is_rejected():
    with response_server(200, b"x" * 101) as (url, requests):
        with pytest.raises(ProcessTransportError, match="byte limit"):
            await ProcessTransport(url, max_response_bytes=100).execute("space/actor", ["true"], cwd="/workspace")


@pytest.mark.parametrize("url", ["ftp://example.com", "http://user:secret@example.com", "http://example.com?token=secret"])
def test_router_does_not_accept_credentials_or_non_http_url(url):
    with pytest.raises(ValueError):
        ProcessTransport(url)


def issue(name, issuer=None, *, ca=False, dns=None, uri=None):
    """A key and certificate, self-signed for a CA, otherwise signed by issuer=(key, certificate)."""
    x509 = pytest.importorskip("cryptography.x509")
    from datetime import datetime, timedelta, timezone
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    signer = issuer[0] if issuer else key
    # Python's default context verifies strictly, so include the extensions real issuers set.
    builder = (x509.CertificateBuilder().subject_name(subject).public_key(key.public_key())
               .issuer_name(issuer[1].subject if issuer else subject).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=not ca, key_cert_sign=ca, crl_sign=ca, content_commitment=False,
                                            key_encipherment=False, data_encipherment=False, key_agreement=False,
                                            encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False))
    names = [x509.DNSName(dns)] if dns else [x509.UniformResourceIdentifier(uri)] if uri else []
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    return key, builder.sign(signer, hashes.SHA256())


def pem(key=None, certificate=None):
    from cryptography.hazmat.primitives import serialization
    out = b""
    if key is not None:
        out += key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    if certificate is not None:
        out += certificate.public_bytes(serialization.Encoding.PEM)
    return out


@contextmanager
def mutual_tls_router(tmp_path, ca):
    """A router stand-in that, like Substrate's, requires a client certificate signed by its CA."""
    import ssl
    clients = []
    server_key, server_cert = issue("router", ca, dns="localhost")
    (tmp_path / "server.pem").write_bytes(pem(server_key, server_cert))
    (tmp_path / "clients-ca.pem").write_bytes(pem(certificate=ca[1]))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.pem")
    context.load_verify_locations(tmp_path / "clients-ca.pem")
    context.verify_mode = ssl.CERT_REQUIRED

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            clients.append(dict(self.connection.getpeercert()["subjectAltName"])["URI"])
            body = b'{"stdout":"ok","stderr":"","exitCode":0}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://localhost:{server.server_port}", clients
        finally:
            server.shutdown()
            thread.join(timeout=5)


async def test_router_tls_presents_the_pods_certificate_and_follows_rotation(tmp_path):
    import os
    from agent_substrate.transport import RouterTLS
    ca = issue("pod identity and service DNS", ca=True)
    bundle, router_ca = tmp_path / "credential-bundle.pem", tmp_path / "router-ca.pem"
    # A projected podCertificate volume writes the private key, then the certificate chain.
    bundle.write_bytes(pem(*issue("pod", ca, uri="spiffe://cluster.local/ns/apps/sa/first")))
    router_ca.write_bytes(pem(certificate=ca[1]))
    with mutual_tls_router(tmp_path, ca) as (url, clients):
        transport = ProcessTransport(url, tls=RouterTLS(credential_bundle=bundle, ca_bundle=router_ca))
        assert (await transport.execute("space/actor", ["true"], cwd="/")).stdout == b"ok"
        # The kubelet replaces the bundle before the certificate expires.
        bundle.write_bytes(pem(*issue("pod", ca, uri="spiffe://cluster.local/ns/apps/sa/second")))
        stat = bundle.stat()
        os.utime(bundle, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        await transport.execute("space/actor", ["true"], cwd="/")
    assert clients == ["spiffe://cluster.local/ns/apps/sa/first", "spiffe://cluster.local/ns/apps/sa/second"]


async def test_router_tls_refuses_a_router_it_cannot_verify(tmp_path):
    from agent_substrate.transport import RouterTLS
    ca, other = issue("cluster", ca=True), issue("elsewhere", ca=True)
    bundle, router_ca = tmp_path / "credential-bundle.pem", tmp_path / "router-ca.pem"
    bundle.write_bytes(pem(*issue("pod", ca, uri="spiffe://cluster.local/ns/apps/sa/agent")))
    router_ca.write_bytes(pem(certificate=other[1]))
    with mutual_tls_router(tmp_path, ca) as (url, clients):
        with pytest.raises(ProcessTransportError):
            await ProcessTransport(url, tls=RouterTLS(credential_bundle=bundle, ca_bundle=router_ca)).execute(
                "space/actor", ["true"], cwd="/")
    assert clients == []


def test_router_tls_needs_https_and_readable_credentials(tmp_path):
    from agent_substrate.transport import RouterTLS
    tls = RouterTLS(credential_bundle=tmp_path / "missing.pem", ca_bundle=tmp_path / "missing-ca.pem")
    with pytest.raises(ValueError, match="https"):
        ProcessTransport("http://router.example", tls=tls)
    with pytest.raises(FileNotFoundError):
        ProcessTransport("https://router.example", tls=tls)
