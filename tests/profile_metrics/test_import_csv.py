from hashlib import sha256

import pytest

from frontend.transaction_import import SAMPLE, parse_csv


def test_csv_preserves_header_and_physical_lines_for_server_error_mapping():
    header, row = SAMPLE.strip().splitlines()
    raw = ("\ufeff" + header + "\r\n" + row + "\r\n\r\n" +
           row.replace("bank-001", '"bank-\n002"') + "\r\n" + row).encode()
    file_hash, items, lines, errors = parse_csv(raw)
    assert file_hash == sha256(raw).hexdigest()
    assert lines == [2, 4, 6]
    assert not errors
    assert items[1]["externalId"] == "bank-\n002"
    assert items[0]["amount"] == "125.50"
    assert items[0]["occurredAt"] == "2026-11-01T09:00:00+00:00"
    assert items[-1]["idempotencyKey"] == f"csv:{file_hash}:6"


def test_csv_decimal_comma_and_local_error():
    header, row = SAMPLE.strip().replace(",", ";").splitlines()
    raw = "\n".join((header, row.replace("125.50", "125,50"),
                     row.replace("125.50", "-3.141"))).encode()
    _, items, lines, errors = parse_csv(raw)
    assert lines == [2] and items[0]["amount"] == "125.50"
    assert [error["Строка файла"] for error in errors] == [3]


@pytest.mark.parametrize("data", [b"wrong\na", b"\xff", SAMPLE.splitlines()[0].encode()])
def test_unusable_csv_is_rejected(data):
    with pytest.raises(ValueError):
        parse_csv(data)
