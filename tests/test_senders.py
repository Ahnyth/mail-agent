from pathlib import Path
from typing import Any

import pytest

from mail_agent.config import ConfigError
from mail_agent.senders import load_senders, normalize_iban, parse_senders

PROJECT = Path(__file__).parent.parent


def test_shipped_files_are_valid() -> None:
    assert load_senders(PROJECT / "config" / "bekannte_absender.yaml").senders
    directory = load_senders(PROJECT / "tests" / "eval" / "bekannte_absender.yaml")
    assert directory.own_domains == ("musterpflege.example",)


def test_lookup_by_address_domain_and_subdomain() -> None:
    directory = parse_senders(
        {
            "eigene_domains": ["musterpflege.example"],
            "absender": [
                {"name": "Kasse", "typ": "kostentraeger", "domains": ["kasse.example"]},
                {"name": "News", "typ": "newsletter", "adressen": ["News@Verlag.example"]},
                {
                    "name": "Lieferant",
                    "typ": "lieferant",
                    "domains": ["@lief.example"],
                    "iban": ["de89 3704"],
                },
            ],
        }
    )
    assert directory.lookup("info@kasse.example").name == "Kasse"  # type: ignore[union-attr]
    assert directory.lookup("a@portal.kasse.example").name == "Kasse"  # type: ignore[union-attr]
    assert directory.lookup("news@verlag.example").name == "News"  # type: ignore[union-attr]
    assert directory.lookup("other@verlag.example") is None
    # lookalike domains are not known
    assert directory.lookup("info@kasse-service.example") is None
    assert directory.lookup("info@kasse.example.evil.example") is None
    assert directory.lookup("x@lief.example").ibans == ("DE893704",)  # type: ignore[union-attr]
    assert directory.is_internal("pdl@musterpflege.example")
    assert not directory.is_internal("pdl@musterpflege.example.org")


def test_normalize_iban() -> None:
    assert normalize_iban(" de89 3704 0044\t0532 0130 00 ") == "DE89370400440532013000"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "Zuordnung"),
        ({"unbekannt": 1}, "unbekannte Schlüssel"),
        ({"absender": {}}, "Liste"),
        ({"absender": [{"typ": "lieferant", "domains": ["a.example"]}]}, "Namen"),
        ({"absender": [{"name": "A", "typ": "bank", "domains": ["a.example"]}]}, "typ"),
        ({"absender": [{"name": "A", "typ": "lieferant"}]}, "domains oder adressen"),
        ({"absender": [{"name": "A", "typ": "lieferant", "domains": "a.example"}]}, "Liste von Texten"),
        ({"absender": [{"name": "A", "typ": "lieferant", "domains": ["a"], "passwort": "x"}]}, "unbekannte"),
    ],
)
def test_invalid_files_are_rejected(data: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_senders(data)


def test_missing_or_broken_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nicht gefunden"):
        load_senders(tmp_path / "fehlt.yaml")
    broken = tmp_path / "kaputt.yaml"
    broken.write_text("absender: [", encoding="utf-8")
    with pytest.raises(ConfigError, match="ungültig"):
        load_senders(broken)
    empty = tmp_path / "leer.yaml"
    empty.write_text("", encoding="utf-8")
    assert load_senders(empty).senders == ()


# --- senders saved in the web page, shared sending services ------------------------------------


def test_invoice_service_is_identified_by_its_reply_to() -> None:
    from mail_agent.senders import identity_address, is_freemail, sent_via_service

    assert sent_via_service("versand@belege.lexware.de")
    assert (
        identity_address("versand@belege.lexware.de", "Werkstatt@Freemail.example")
        == "werkstatt@freemail.example"
    )
    assert identity_address("info@firma.example", "andere@web.de") == "info@firma.example"
    assert is_freemail("max@gmx.de")
    assert not is_freemail("info@kanzlei.example")
    directory = parse_senders(
        {"absender": [{"name": "Werkstatt", "typ": "lieferant", "adressen": ["werkstatt@freemail.example"]}]}
    )
    found = directory.lookup_message("versand@belege.lexware.de", "werkstatt@freemail.example")
    assert found is not None
    assert found.name == "Werkstatt"
    # another company that also sends via Lexware is not known
    assert directory.lookup_message("versand@belege.lexware.de", "betrug@freemail.example") is None


def test_saved_senders_round_trip_replace_and_remove(tmp_path: Path) -> None:
    from mail_agent.senders import (
        KnownSender,
        load_local_senders,
        remove_local_sender,
        save_local_sender,
    )

    path = tmp_path / "daten" / "bekannte_absender.yaml"
    assert load_local_senders(path).senders == ()
    save_local_sender(path, KnownSender("Kanzlei", "anwalt", domains=("kanzlei.example",)))
    save_local_sender(path, KnownSender("Kanzlei Muster", "anwalt", domains=("kanzlei.example",)))  # replaces
    save_local_sender(
        path,
        KnownSender("Werkstatt", "lieferant", addresses=("w@web.de",), ibans=("DE89370400440532013000",)),
    )
    senders = load_local_senders(path).senders
    assert [s.name for s in senders] == ["Kanzlei Muster", "Werkstatt"]
    assert senders[1].ibans == ("DE89370400440532013000",)
    assert path.read_text(encoding="utf-8").startswith("# Über die Weboberfläche")
    assert remove_local_sender(path, "kanzlei.example")
    assert not remove_local_sender(path, "kanzlei.example")
    assert [s.name for s in load_local_senders(path).senders] == ["Werkstatt"]


def test_saved_senders_win_over_the_shared_list() -> None:
    shared = parse_senders({"absender": [{"name": "Alt", "typ": "sonstige", "domains": ["firma.example"]}]})
    local = parse_senders({"absender": [{"name": "Neu", "typ": "lieferant", "domains": ["firma.example"]}]})
    found = shared.merged(local).lookup("info@firma.example")
    assert found is not None
    assert found.name == "Neu"


def test_lawyer_is_a_sender_type() -> None:
    directory = parse_senders(
        {"absender": [{"name": "Kanzlei", "typ": "anwalt", "domains": ["kanzlei.example"]}]}
    )
    found = directory.lookup("ra@kanzlei.example")
    assert found is not None
    assert found.type == "anwalt"
