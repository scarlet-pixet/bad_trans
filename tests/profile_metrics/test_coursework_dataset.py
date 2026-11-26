from datetime import date
from io import BytesIO
import tarfile

from tools.import_tabformer import read_transactions


def test_tabformer_archive_import_is_deterministic_and_excludes_failed_charges(tmp_path):
    source = tmp_path / "transactions.tgz"
    headers = "User,Card,Year,Month,Day,Time,Amount,Merchant Name,Errors?,Is Fraud?\n"
    rows = (
        "0,1,2019,7,1,06:16,$42.95,5,,No\n"
        "0,1,2019,7,1,06:16,$42.95,5,,No\n"
        "0,1,2019,7,2,06:00,$-14.20,7,,No\n"
        "0,1,2019,7,3,02:30,$300.00,2,Insufficient Balance,No\n"
        "0,0,2019,7,4,16:26,$22.01,2,,Yes\n"
        "1,0,2019,7,6,16:26,$100.01,2,,Yes\n"
    )
    data = (headers+rows).encode()
    with tarfile.open(source,"w:gz") as archive:
        member = tarfile.TarInfo("card_transaction.v1.csv")
        member.size = len(data)
        archive.addfile(member, BytesIO(data))
    args = (source, {"0"}, date(2019,7,1), date(2019,8,1), 9)
    selected, ignored = read_transactions(*args)
    assert read_transactions(*args) == (selected, ignored)
    assert ignored == {"declined":1,"nonPositive":1}
    assert len({row["externalId"] for row in selected}) == 3
    assert selected[0]["occurredAt"] == "2019-07-01T06:16:00+00:00"
    assert selected[-1]["label"] == "Yes"
