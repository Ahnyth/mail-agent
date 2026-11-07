"""Known senders and domains from ``config/bekannte_absender.yaml``.

Known senders get no blanket fraud warning; for suppliers the deposited IBANs are compared with the
IBANs in a message. The file contains no secrets; unknown keys and types are rejected.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml

from .config import ConfigError

SenderType = Literal[
    "kostentraeger", "abrechnungsdienst", "lieferant", "behoerde", "arzt", "anwalt", "newsletter",
    "sonstige",
]  # fmt: skip
"""The kinds of known senders; the value of ``typ`` in the file."""
SENDER_TYPES: tuple[str, ...] = get_args(SenderType)
"""The kinds of known senders in the order of the selection list."""
SENDER_TYPE_LABELS: dict[str, str] = {
    "kostentraeger": "Kostenträger (Kasse)",
    "abrechnungsdienst": "Abrechnungsdienst",
    "lieferant": "Lieferant / Dienstleister",
    "behoerde": "Behörde",
    "arzt": "Arzt / Klinik",
    "anwalt": "Anwalt / Kanzlei",
    "newsletter": "Newsletter / Werbung",
    "sonstige": "Sonstiger bekannter Absender",
}
"""The German name of every kind of sender, as shown in the interface."""

SHARED_SENDING_DOMAINS: tuple[str, ...] = (
    "lexware.de", "lexoffice.de", "sevdesk.de", "sevdesk.email", "debitoor.de", "fastbill.com",
    "sendgrid.net", "mcsv.net", "mailchimpapp.net", "sendinblue.com", "brevo.com", "amazonses.com",
)  # fmt: skip
"""Invoice and newsletter services that send on behalf of many companies (From: versand@belege.lexware.de).

Such a From address says nothing about the company; the Reply-To address identifies it.
"""
FREEMAIL_DOMAINS: tuple[str, ...] = (
    "gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "t-online.de",
    "freenet.de", "yahoo.de", "yahoo.com", "outlook.com", "outlook.de", "hotmail.com", "hotmail.de",
    "live.com", "live.de", "icloud.com", "me.com", "aol.com", "aol.de", "mail.de", "posteo.de",
    "online.de", "arcor.de", "vodafonemail.de", "magenta.de", "proton.me", "protonmail.com",
)  # fmt: skip
"""Mail providers for everyone: never "the whole domain" as a known sender, only the exact address."""

_FILE_KEYS = frozenset({"eigene_domains", "absender"})
"""The keys allowed at the top of the file."""
_SENDER_KEYS = frozenset({"name", "typ", "domains", "adressen", "iban"})
"""The keys allowed in one entry; anything else is rejected, so a typo cannot pass unnoticed."""
_COMPANY_DOMAIN_LABELS = 2
"""Labels of the domain a company owns: name and top-level domain (``firma.de``)."""
_WHITESPACE = re.compile(r"\s+")
"""Spaces, tabs and line breaks inside an IBAN."""


def normalize_iban(value: str) -> str:
    """Returns the IBAN without whitespace and in capitals, the form it is compared in."""
    return _WHITESPACE.sub("", value).upper()


def sent_via_service(address: str) -> bool:
    """Checks for the From address of a sending service that many companies share (e.g. Lexware)."""
    return _within(_domain(address), SHARED_SENDING_DOMAINS)


def is_freemail(address: str) -> bool:
    """Checks whether ``address`` belongs to a mail provider for everyone (gmail.com, web.de …)."""
    return _within(_domain(address), FREEMAIL_DOMAINS)


def base_domain(address: str) -> str:
    """Returns the domain a company owns, without mail subdomains: newsletter.firma.de -> firma.de."""
    return ".".join(_domain(address).split(".")[-_COMPANY_DOMAIN_LABELS:])


def identity_address(sender_address: str, reply_to: str) -> str:
    """Returns the address that identifies the company: the Reply-To for shared sending services."""
    reply = reply_to.strip().lower()
    if reply and "@" in reply and sent_via_service(sender_address) and not sent_via_service(reply):
        return reply
    return sender_address.strip().lower()


def _domain(address: str) -> str:
    """Returns the domain of ``address`` in lower case."""
    return address.rpartition("@")[2].strip().lower()


def _within(domain: str, candidates: tuple[str, ...]) -> bool:
    """Checks whether ``domain`` is one of ``candidates`` or a subdomain of one; lookalikes do not match."""
    return any(domain == d or domain.endswith(f".{d}") for d in candidates)


@dataclass(frozen=True)
class KnownSender:
    """One entry of the list of known senders."""

    name: str
    type: SenderType
    domains: tuple[str, ...] = ()
    """Domains of the sender in lower case; subdomains count too."""
    addresses: tuple[str, ...] = ()
    """Single addresses in lower case, e.g. at a freemail provider."""
    ibans: tuple[str, ...] = ()
    """The deposited IBANs, normalized; a different IBAN in a mail raises a warning."""


@dataclass(frozen=True)
class SenderDirectory:
    """The known senders and the own domains."""

    own_domains: tuple[str, ...] = ()
    senders: tuple[KnownSender, ...] = ()

    def lookup(self, address: str) -> KnownSender | None:
        """Returns the sender of ``address``: exact address first, then the domain (subdomains included).

        Lookalike domains (``kasse-service.example`` for ``kasse.example``) do not match.
        """
        address = address.strip().lower()
        for sender in self.senders:
            if address in sender.addresses:
                return sender
        domain = _domain(address)
        for sender in self.senders:
            if _within(domain, sender.domains):
                return sender
        return None

    def lookup_message(self, sender_address: str, reply_to: str) -> KnownSender | None:
        """Returns the sender like :meth:`lookup`; a mail from a shared sending service counts by Reply-To."""
        return self.lookup(identity_address(sender_address, reply_to))

    def is_internal(self, address: str) -> bool:
        """Checks whether ``address`` belongs to an own domain (subdomains included)."""
        return _within(_domain(address), self.own_domains)

    def merged(self, other: "SenderDirectory") -> "SenderDirectory":
        """Returns both directories in one; ``other`` (saved in the web page) comes first, so it wins."""
        return SenderDirectory(
            own_domains=tuple(dict.fromkeys((*self.own_domains, *other.own_domains))),
            senders=(*other.senders, *self.senders),
        )


# --- reading the file ---------------------------------------------------------------------------------------


def load_senders(path: Path) -> SenderDirectory:
    """Reads a file of known senders.

    Args:
        path: The YAML file.

    Returns:
        The known senders and own domains of the file.

    Raises:
        ConfigError: The file is missing, is no valid YAML or has invalid content.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Datei mit bekannten Absendern nicht gefunden: {path.name}") from None
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Datei mit bekannten Absendern ungültig: {exc}") from None
    return parse_senders(data)


def parse_senders(data: object) -> SenderDirectory:
    """Checks the content of a file of known senders and builds the directory.

    Args:
        data: The YAML content: ``eigene_domains`` and ``absender`` (a list of entries); ``None`` if empty.

    Returns:
        The known senders and own domains; empty for an empty file.

    Raises:
        ConfigError: An unknown key, a missing name or type, or a value of the wrong kind.
    """
    if data is None:
        return SenderDirectory()
    if not isinstance(data, dict):
        raise ConfigError("bekannte Absender: die Datei muss eine Zuordnung enthalten")
    unknown = set(data) - _FILE_KEYS
    if unknown:
        raise ConfigError(f"bekannte Absender: unbekannte Schlüssel {', '.join(sorted(map(str, unknown)))}")
    senders = tuple(_parse_entry(index, raw) for index, raw in enumerate(_entries(data), start=1))
    own = _domains(_strings(data.get("eigene_domains"), "eigene_domains"))
    return SenderDirectory(own_domains=own, senders=senders)


def _entries(data: dict[Any, Any]) -> list[Any]:
    """Returns the list under ``absender``; a missing or empty value is an empty list."""
    entries = data.get("absender", [])
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ConfigError("bekannte Absender: absender muss eine Liste sein")
    return entries


def _parse_entry(index: int, raw: object) -> KnownSender:
    """Checks entry number ``index`` of the list and builds the sender; raises :class:`ConfigError`."""
    entry = _entry_mapping(index, raw)
    name, sender_type = entry.get("name"), entry.get("typ")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"bekannte Absender: Eintrag {index} braucht einen Namen")
    if sender_type not in SENDER_TYPES:
        raise ConfigError(f"bekannte Absender: {name}: typ muss einer von {', '.join(SENDER_TYPES)} sein")
    domains = _domains(_strings(entry.get("domains"), "domains"))
    addresses = tuple(a.lower() for a in _strings(entry.get("adressen"), "adressen"))
    if not domains and not addresses:
        raise ConfigError(f"bekannte Absender: {name}: domains oder adressen angeben")
    return KnownSender(
        name=name.strip(),
        type=cast(SenderType, sender_type),
        domains=domains,
        addresses=addresses,
        ibans=tuple(normalize_iban(i) for i in _strings(entry.get("iban"), "iban")),
    )


def _entry_mapping(index: int, raw: object) -> dict[str, Any]:
    """Returns entry number ``index`` as a mapping with known keys only; raises :class:`ConfigError`."""
    if not isinstance(raw, dict):
        raise ConfigError(f"bekannte Absender: Eintrag {index} ist keine Zuordnung")
    entry = cast(dict[str, Any], raw)
    unknown = set(entry) - _SENDER_KEYS
    if unknown:
        raise ConfigError(f"bekannte Absender: Eintrag {index} hat unbekannte Schlüssel {sorted(unknown)}")
    return entry


def _strings(value: object, key: str) -> tuple[str, ...]:
    """Returns a list of non-empty texts as a tuple; a missing value is empty, anything else is rejected."""
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"bekannte Absender: {key} muss eine Liste von Texten sein")
    return tuple(cast(list[str], value))


def _domains(values: tuple[str, ...]) -> tuple[str, ...]:
    """Returns domains in lower case; a leading ``@`` (as in ``@firma.de``) is dropped."""
    return tuple(d.lower().lstrip("@") for d in values)


# --- senders saved from the web page ("Absender als bekannt hinterlegen") -----------------------------------

LOCAL_FILE_HEADER = """\
# Über die Weboberfläche hinterlegte bekannte Absender („Absender als bekannt hinterlegen“).
# Wird vom MailAgent geschrieben; liegt wie die Datenbank nur auf diesem PC.
# Dauerhaft für alle PCs: Einträge nach config/bekannte_absender.yaml übernehmen.
"""
"""Comment at the top of the local file, so that whoever opens it knows where it comes from."""


def load_local_senders(path: Path) -> SenderDirectory:
    """Reads the senders saved in the web page; no file means none saved yet."""
    return load_senders(path) if path.is_file() else SenderDirectory()


def save_local_sender(path: Path, sender: KnownSender) -> None:
    """Adds ``sender``; an entry for the same domain or address is replaced (latest decision wins)."""
    keys = {*sender.domains, *sender.addresses}
    kept = [s for s in load_local_senders(path).senders if not keys & {*s.domains, *s.addresses}]
    _write_local(path, [*kept, sender])


def remove_local_sender(path: Path, identity: str) -> bool:
    """Removes the saved entry that covers ``identity`` (address or domain); ``False`` if there is none."""
    senders = list(load_local_senders(path).senders)
    kept = [s for s in senders if identity not in s.addresses and identity not in s.domains]
    if len(kept) == len(senders):
        return False
    _write_local(path, kept)
    return True


def _entry(sender: KnownSender) -> dict[str, object]:
    """Returns ``sender`` in the form of the file; empty lists are left out."""
    entry: dict[str, object] = {"name": sender.name, "typ": sender.type}
    if sender.domains:
        entry["domains"] = list(sender.domains)
    if sender.addresses:
        entry["adressen"] = list(sender.addresses)
    if sender.ibans:
        entry["iban"] = list(sender.ibans)
    return entry


def _write_local(path: Path, senders: list[KnownSender]) -> None:
    """Writes the local file with its header comment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"absender": [_entry(s) for s in senders]},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    # Write beside and then replace: a crash in between never leaves a half-written file behind.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(LOCAL_FILE_HEADER + body, encoding="utf-8")
    temporary.replace(path)
