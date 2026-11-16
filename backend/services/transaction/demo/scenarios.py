"""Reproducible synthetic banking source. Labels never enter detector inputs."""

from datetime import datetime, timedelta
from random import Random


def scenario_records(run_key: str, anchor: datetime) -> list[dict]:
    random = Random(run_key)
    records = []
    for client in range(3):
        for day in range(12):
            records.append(
                {
                    "client": client,
                    "recipient": "usual",
                    "expected": None,
                    "scenario": "history",
                    "phase": "HISTORY",
                    "input": {
                        "amount": str(90 + random.randrange(21)),
                        "occurredAt": (
                            anchor - timedelta(days=14 - day, hours=2 - client)
                        ).isoformat(),
                    },
                }
            )
        for index in range(3):
            records.append(
                {
                    "client": client,
                    "recipient": "usual",
                    "expected": False,
                    "scenario": "usual_payment",
                    "phase": "LIVE",
                    "input": {
                        "amount": "100.00",
                        "occurredAt": (
                            anchor - timedelta(hours=8 - client, minutes=30 - index)
                        ).isoformat(),
                    },
                }
            )
        for index in range(2):
            records.append(
                {
                    "client": client,
                    "recipient": "new" if index else "usual",
                    "expected": True,
                    "scenario": "new_recipient_spike" if index else "amount_spike",
                    "phase": "LIVE",
                    "input": {
                        "amount": "5000.00",
                        "occurredAt": (
                            anchor - timedelta(hours=3 - client, minutes=20 - index)
                        ).isoformat(),
                    },
                }
            )
        # A labeled frequency anomaly can expose false negatives in the current
        # weight policy. Quality figures must not be engineered to look perfect.
        for index in range(6):
            records.append(
                {
                    "client": client,
                    "recipient": "usual",
                    "expected": index >= 4,
                    "scenario": "frequency_burst",
                    "phase": "LIVE",
                    "input": {
                        "amount": "100.00",
                        "occurredAt": (
                            anchor - timedelta(minutes=20 - client * 3 - index)
                        ).isoformat(),
                    },
                }
            )
    return sorted(records, key=lambda r: r["input"]["occurredAt"])
