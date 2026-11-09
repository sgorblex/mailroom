"""A scripted SMTP server, for what Mailpit will not do: refusing part of the recipients, going
quiet or dropping the line at a chosen step, or not offering TLS.

It speaks real TLS, with a certificate made on the spot for `localhost` and `127.0.0.1`, since a
client may refuse to talk without it: `SmtpStub.trusted_context()` is a verifying client context
that trusts it.
"""

import socket
import ssl
import tempfile
import threading
from base64 import b64decode
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from functools import cache
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from types import TracebackType
    from typing import BinaryIO

STALL = None
"""As a reply: never answer, keeping the connection open."""
HANG_UP = b""
"""As a reply: close the connection instead of answering."""

type Reply = bytes | None
type TlsMode = Literal["implicit", "starttls", "stripped"]
"""`stripped`: plaintext, not offering STARTTLS, as a man in the middle would leave it."""

_DAY = timedelta(days=1)
_REFUSED = b"550 refused secret words\r\n"


@cache
def _certificate() -> tuple[bytes, bytes]:
    """A self-signed certificate and its key, PEM, that passes a verifying client's checks."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    public = key.public_key()
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _DAY)
        .not_valid_after(now + _DAY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(public), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def _server_context() -> ssl.SSLContext:
    certificate, key = _certificate()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "stub.pem"
        path.write_bytes(certificate + key)
        context.load_cert_chain(path)
    return context


class SmtpStub:
    """Serves on 127.0.0.1 until the `with` block ends, one thread per connection. Any login goes,
    unless `auth_reply` says otherwise. Each reply is the bytes to write, or `STALL` or `HANG_UP`.

    What it saw: `commands` (each line, AUTH's without its arguments), `messages` (each message's
    bytes with the dots unstuffed), `raw_messages` (as they came) and `logins`
    (`\\0login\\0password`).
    """

    def __init__(
        self,
        *,
        tls: TlsMode = "implicit",
        auth: str = "PLAIN LOGIN",
        auth_reply: Reply = b"235 ok\r\n",
        starttls_reply: Reply = b"220 go ahead\r\n",
        tls_handshake: bool = True,
        mail_reply: Reply = b"250 ok\r\n",
        refuse: Collection[str] = (),
        rcpt_reply: Reply = b"250 ok\r\n",
        data_reply: Reply = b"354 send it\r\n",
        end_reply: Reply = b"250 queued\r\n",
        end_drip: float | None = None,
        quit_reply: Reply = b"221 bye\r\n",
    ) -> None:
        """`tls_handshake` False goes quiet after answering STARTTLS. `refuse` lists recipients
        answered 550. `end_reply` answers the message itself;
        `end_drip`, in seconds, instead sends a byte of a reply that never ends at each step of
        that length, which keeps any socket timeout from firing."""
        self._tls: TlsMode = tls
        self._auth = auth
        self._auth_reply = auth_reply
        self._starttls_reply = starttls_reply
        self._tls_handshake = tls_handshake
        self._mail_reply = mail_reply
        self._refuse = frozenset(refuse)
        self._rcpt_reply = rcpt_reply
        self._data_reply = data_reply
        self._end_reply = end_reply
        self._end_drip = end_drip
        self._quit_reply = quit_reply
        self.commands: list[str] = []
        self.messages: list[bytes] = []
        self.raw_messages: list[bytes] = []
        self.logins: list[bytes] = []
        self._stop = threading.Event()
        self._context = _server_context()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.05)  # so `accept` sees the stop
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self.port: int = self._listener.getsockname()[1]

    def __enter__(self) -> Self:
        self._spawn(self._accept)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._listener.close()
        for connection in self._sockets:
            # A close alone would leave the reader on `makefile` blocked.
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        for thread in self._threads:
            thread.join(5)

    @staticmethod
    def trusted_context() -> ssl.SSLContext:
        """A client context that verifies certificates and names, and trusts this stub's."""
        return ssl.create_default_context(cadata=_certificate()[0].decode("ascii"))

    def smtp(self, tls: Literal["implicit", "starttls"] | None = None) -> dict[str, object]:
        """The `smtp` of an account on this stub."""
        mode = tls or ("implicit" if self._tls == "implicit" else "starttls")
        return {"host": "localhost", "port": self.port, "tls": mode}

    def _spawn(self, target: Callable[..., None], *arguments: object) -> None:
        thread = threading.Thread(target=target, args=arguments, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            connection.settimeout(None)
            self._sockets.append(connection)
            self._spawn(self._serve, connection)

    def _serve(self, connection: socket.socket) -> None:
        try:
            if self._tls == "implicit":
                connection = self._context.wrap_socket(connection, server_side=True)
                self._sockets.append(connection)
            self._conversation(connection)
        except OSError:
            pass  # the client went away, which is what a test may be about
        finally:
            connection.close()

    def _say(self, connection: socket.socket, reply: Reply) -> bool:
        """Whether the conversation goes on after this reply."""
        if reply is STALL:
            self._stop.wait()
            return False
        if reply == HANG_UP:
            return False
        connection.sendall(reply)
        return True

    def _ehlo(self, secured: bool) -> bytes:
        lines = ["localhost", "SIZE 104857600"]
        if self._tls == "starttls" and not secured:
            lines.append("STARTTLS")
        if self._tls != "starttls" or secured:
            lines.append(f"AUTH {self._auth}")
        last = len(lines) - 1
        text = "".join(
            f"250{' ' if index == last else '-'}{line}\r\n" for index, line in enumerate(lines)
        )
        return text.encode("ascii")

    def _conversation(self, connection: socket.socket) -> None:
        secured = self._tls == "implicit"
        lines = connection.makefile("rb")
        connection.sendall(b"220 stub ready\r\n")
        while not self._stop.is_set():
            line = lines.readline()
            if not line:
                return
            command, _, argument = line.rstrip(b"\r\n").partition(b" ")
            verb = command.upper()
            self.commands.append(
                "AUTH" if verb == b"AUTH" else line.rstrip(b"\r\n").decode("ascii", "replace")
            )
            if verb in (b"EHLO", b"HELO"):
                reply: Reply = self._ehlo(secured)
            elif verb == b"STARTTLS":
                if not self._say(connection, self._starttls_reply):
                    return
                if self._starttls_reply != b"220 go ahead\r\n":
                    continue
                if not self._tls_handshake:
                    self._stop.wait()
                    return
                connection = self._context.wrap_socket(connection, server_side=True)
                self._sockets.append(connection)
                lines = connection.makefile("rb")
                secured = True
                continue
            elif verb == b"AUTH":
                self.logins.append(_login(connection, lines, argument))
                reply = self._auth_reply
            elif verb == b"MAIL":
                reply = self._mail_reply
            elif verb == b"RCPT":
                address = argument.partition(b"<")[2].partition(b">")[0].decode("ascii", "replace")
                reply = _REFUSED if address in self._refuse else self._rcpt_reply
            elif verb == b"DATA":
                if not self._say(connection, self._data_reply):
                    return
                if self._data_reply != b"354 send it\r\n":
                    continue
                raw = _raw_message(lines)
                self.raw_messages.append(raw)
                self.messages.append(_unstuffed(raw))
                reply = self._ending(connection)
            elif verb == b"QUIT":
                self._say(connection, self._quit_reply)
                return
            else:
                reply = b"250 ok\r\n"
            if not self._say(connection, reply):
                return

    def _ending(self, connection: socket.socket) -> Reply:
        if self._end_drip is None:
            return self._end_reply
        while not self._stop.wait(self._end_drip):
            connection.sendall(b"2")
        return HANG_UP


def _login(connection: socket.socket, lines: BinaryIO, argument: bytes) -> bytes:
    """AUTH PLAIN's credentials as sent, or LOGIN's, asked one by one and joined the same way."""
    mechanism, _, initial = argument.partition(b" ")
    if mechanism.upper() != b"LOGIN":
        return b64decode(initial)
    answers = [b""]
    for prompt in (b"334 VXNlcm5hbWU6\r\n", b"334 UGFzc3dvcmQ6\r\n"):
        connection.sendall(prompt)
        answers.append(b64decode(lines.readline().strip()))
    return b"\0".join(answers)


def _raw_message(lines: BinaryIO) -> bytes:
    received: list[bytes] = []
    while True:
        line = lines.readline()
        if not line or line == b".\r\n":
            return b"".join(received)
        received.append(line)


def _unstuffed(raw: bytes) -> bytes:
    return b"".join(
        line[1:] if line.startswith(b".") else line for line in raw.splitlines(keepends=True)
    )
