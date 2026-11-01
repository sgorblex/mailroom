"""BODYSTRUCTURE analysis (`src/mail/structure.ts`): which part is the body, which are
attachments, which is a calendar invite, so a read downloads only the part it shows."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from mailroom_core.mail._mime import parameters, text_value
from mailroom_core.render import PartInfo

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class MimePart:
    """One leaf of the structure, with what decoding its content takes."""

    part: str
    """The IMAP part number (`1`, `1.2`), what `part_content` and the attachment calls take."""
    type: str
    """Lowercase, `text/plain`."""
    size: int
    """Encoded size in bytes, as the server reports it: what a download transfers."""
    name: str | None
    """The filename (Content-Disposition), else the Content-Type `name`, decoded."""
    encoding: str
    """Content-Transfer-Encoding, lowercase."""
    charset: str | None
    disposition: str | None
    """Lowercase, `attachment` or `inline`."""
    content_id: str | None
    """Without its angle brackets."""
    flowed: bool = False
    delsp: bool = False

    @property
    def decoded_size(self) -> int:
        """The content's size once decoded, estimated: base64 takes 78 bytes per 57 with its
        76-character lines; quoted-printable is taken at its encoded size, an upper bound."""
        if self.encoding == "base64":
            return self.size * 57 // 78
        return self.size

    @property
    def info(self) -> PartInfo:
        """With the decoded size estimate: the one size hosts show, as a download's is exact."""
        return PartInfo(part=self.part, type=self.type, size=self.decoded_size, name=self.name)


@dataclass(frozen=True, slots=True)
class Structure:
    body: MimePart | None
    """The body part to show: the first unnamed inline text/plain, else such a text/html."""
    html: MimePart | None
    """The first unnamed inline text/html, the message's HTML body, whichever part is shown."""
    attachments: tuple[PartInfo, ...]
    """Every other leaf but the inline parts; an attached message (message/rfc822) is one, not
    descended into."""
    inline_parts: tuple[PartInfo, ...]
    """Parts of a multipart/related with a Content-ID and no attachment disposition: what the
    HTML shows in place (`cid:` images), not attachments (the TypeScript lists them as such)."""
    calendar: PartInfo | None
    """The first text/calendar part: an invite, not parsed yet."""
    parts: Mapping[str, MimePart]
    """Every leaf by part number."""

    @property
    def body_format(self) -> Literal["text", "html"] | None:
        if self.body is None:
            return None
        return "html" if self.body.type == "text/html" else "text"

    @property
    def downloadable(self) -> tuple[PartInfo, ...]:
        """The parts an attachment call may return: the attachments, the inline parts, then the
        invite."""
        calendar = () if self.calendar is None else (self.calendar,)
        return self.attachments + self.inline_parts + calendar


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _leaf(node: Sequence[object], part: str) -> MimePart:
    first = node[0] if len(node) > 0 else None
    main = text_value(first if isinstance(first, bytes | str) else b"").lower()
    sub = text_value(node[1] if len(node) > 1 and isinstance(node[1], bytes | str) else b"")
    kind = f"{main}/{sub.lower()}"
    params = parameters(node[2] if len(node) > 2 else None)
    content_id = node[3] if len(node) > 3 and isinstance(node[3], bytes | str) else None
    encoding = node[5] if len(node) > 5 and isinstance(node[5], bytes | str) else b"7bit"
    # Extension fields start after the type-specific ones: text/* has a line count, an attached
    # message its envelope, body and line count (RFC 3501, 7.4.2).
    if main == "text":
        at = 9
    elif kind == "message/rfc822":
        at = 11
    else:
        at = 8
    raw = node[at] if len(node) > at else None
    disposition: str | None = None
    disposition_params: dict[str, str] = {}
    if isinstance(raw, tuple | list) and raw and isinstance(raw[0], bytes | str):
        disposition = text_value(raw[0]).lower()
        disposition_params = parameters(raw[1] if len(raw) > 1 else None)
    name = disposition_params.get("filename") or params.get("name")
    return MimePart(
        part=part,
        type=kind,
        size=_int(node[6] if len(node) > 6 else 0),
        name=name or None,
        encoding=text_value(encoding).lower().strip(),
        charset=params.get("charset") or None,
        disposition=disposition,
        content_id=(
            None
            if content_id is None
            else text_value(content_id).strip().removeprefix("<").removesuffix(">") or None
        ),
        flowed=params.get("format", "").lower() == "flowed",
        delsp=params.get("delsp", "").lower() == "yes",
    )


def _is_multipart(node: Sequence[object]) -> bool:
    return len(node) > 0 and isinstance(node[0], list)


def analyse_structure(root: Sequence[object]) -> Structure:
    """`root` is imapclient's BODYSTRUCTURE (`BodyData`): a multipart node is
    `([children], subtype, params, …)`, a leaf `(type, subtype, params, id, description,
    encoding, size, …)`. A single-part message has no part number of its own; IMAP calls its body
    part `1`. An empty or missing structure (a server that sent none) has no parts."""
    text: MimePart | None = None
    html: MimePart | None = None
    calendar: MimePart | None = None
    attachments: list[PartInfo] = []
    inline_parts: list[PartInfo] = []
    leaves: dict[str, MimePart] = {}
    if not isinstance(root, tuple | list) or len(root) == 0:
        return Structure(None, None, (), (), None, leaves)

    # Iterative, depth first, children in order: nesting is the sender's to choose. With each
    # node, whether its parent is a multipart/related.
    stack: list[tuple[Sequence[object], str, bool]] = [
        (root, "" if _is_multipart(root) else "1", False)
    ]
    while stack:
        node, part, related = stack.pop()
        if _is_multipart(node):
            children = node[0]
            assert isinstance(children, list)
            subtype = node[1] if len(node) > 1 and isinstance(node[1], bytes | str) else b""
            is_related = text_value(subtype).lower() == "related"
            prefix = "" if part == "" else f"{part}."
            stack.extend(
                (child, f"{prefix}{index}", is_related)
                for index, child in reversed(list(enumerate(children, 1)))
                if isinstance(child, tuple | list) and len(child) > 0
            )
            continue
        leaf = _leaf(node, part)
        leaves[part] = leaf
        inline = leaf.disposition != "attachment" and leaf.name is None
        if inline and leaf.type == "text/plain" and text is None:
            text = leaf
        elif inline and leaf.type == "text/html" and html is None:
            html = leaf
        elif leaf.type == "text/calendar" and calendar is None:
            calendar = leaf
        elif related and leaf.content_id is not None and leaf.disposition != "attachment":
            inline_parts.append(leaf.info)
        else:
            attachments.append(leaf.info)
    return Structure(
        body=text if text is not None else html,
        html=html,
        attachments=tuple(attachments),
        inline_parts=tuple(inline_parts),
        calendar=None if calendar is None else calendar.info,
        parts=leaves,
    )
