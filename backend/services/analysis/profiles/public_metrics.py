"""Keep large audit lists in the saved profile and expose its statistical summary."""

MAX_INLINE_HISTORY_IDS = 1000
MAX_INLINE_EXCLUSIONS = 100


def public_metrics(metrics):
    compact = metrics.copy()
    omitted = {}
    for key in ("history_ids", "observedHistoryIds"):
        if len(compact.get(key, ())) > MAX_INLINE_HISTORY_IDS:
            omitted[key] = len(compact.pop(key))
    if len(compact.get("exclusions", ())) > MAX_INLINE_EXCLUSIONS:
        omitted["exclusions"] = len(compact["exclusions"]) - MAX_INLINE_EXCLUSIONS
        compact["exclusions"] = compact["exclusions"][:MAX_INLINE_EXCLUSIONS]
    if omitted:
        compact["omittedHistoryDetails"] = omitted
    return compact
