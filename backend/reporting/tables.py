"""One presentation model for PDF, XLSX and CSV; never queries source owners."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re

from backend.common.data import DomainError
from backend.common.report_contract import canonical, MAX_BYTES

VERSION = "report-export-v1"
NAMES = {"PROFILE": "Профиль клиента", "OPERATION": "Оценка операции", "PERIOD": "Аналитика за период"}
LABELS = {
    "id": "ID", "clientId": "Клиент ID", "currency": "Валюта", "direction": "Направление",
    "amount": "Сумма", "transactionCount": "Операций", "uniqueClients": "Уникальных клиентов",
    "occurredAt": "Время операции UTC", "ingestedAt": "Время поступления UTC",
    "transaction_id": "Операция ID", "calculated_at": "Время расчёта UTC", "run_id": "Запуск ID",
    "assessment_status": "Полнота оценки", "assessment_score": "Оценка", "score": "Исходный балл",
    "data_status": "Достаточность истории", "review_required": "Нужно рассмотрение",
    "created_at": "Дата создания UTC", "decided_at": "Дата решения UTC", "author_id": "Автор ID",
    "text": "Комментарий", "outcome": "Решение", "reason": "Основание", "review_cycle": "Цикл",
    "count": "Количество", "totalAmount": "Объём", "meanAmount": "Средняя сумма",
    "medianAmount": "Медиана", "madAmount": "MAD", "iqrAmount": "IQR", "sampleSize": "Операций в базе",
    "observedCount": "Наблюдалось", "includedCount": "В базе сравнения", "excludedCount": "Исключено",
    "asOf": "Предметный срез UTC", "windowDays": "Окно, дней", "version": "Версия",
    "origin": "Происхождение", "status": "Состояние", "confidence": "Наполненность выборки",
    "external_id": "Внешний номер", "account_id": "Счёт ID", "counterparty_id": "Контрагент ID",
    "clientName": "Имя клиента", "accountExternalId": "Номер счёта", "counterpartyName": "Контрагент",
    "occurred_at": "Время операции UTC", "ingested_at": "Время поступления UTC", "source": "Источник",
    "denominator": "Знаменатель охвата операций", "resultCount": "Операций с результатом",
    "usableAssessmentCount": "Операций с доступной оценкой", "resultCoverage": "Охват результатами",
    "usableAssessmentCoverage": "Охват доступной оценкой", "profileGroupDenominator": "Групп профилирования",
    "profiledGroups": "Групп с профилем", "readyProfileGroups": "Групп с готовым профилем",
    "caseCount": "Случаев подборки", "newCasesInPeriod": "Новых случаев в периоде",
    "decisionCount": "Решений в периоде", "decidedCases": "Рассмотренных случаев",
}
MONEY = {"amount", "totalamount", "meanamount", "medianamount", "madamount", "iqramount", "q25", "q75", "minamount", "maxamount", "stdamount"}


@dataclass(frozen=True)
class Table:
    key: str
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple, ...]


def scalar(value, key=""):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, str) and key.lower() in MONEY:
        try:
            number = Decimal(value)
            if number.is_finite():
                return number
        except InvalidOperation:
            pass
    return value


def flat(value, prefix="", currency=""):
    if isinstance(value, dict) and value:
        return [r for key, child in value.items() for r in flat(child, (prefix + "." if prefix else "") + str(key), currency)]
    if isinstance(value, list) and value:
        return [r for i, child in enumerate(value) for r in flat(child, f"{prefix}[{i}]", currency)]
    key = prefix.rsplit(".", 1)[-1]
    unit = currency if key.lower() in MONEY else "сек" if "seconds" in key.lower() else ""
    return [(prefix, LABELS.get(key, key), scalar(value, key), unit)]


def fields(key, title, value, currency=""):
    return Table(key, title, ("Поле", "Показатель", "Значение", "Единица"), tuple(flat(value, currency=currency)))


def records(key, title, rows):
    columns = list(dict.fromkeys(k for row in rows for k in row))
    if not columns:
        return Table(key, title, ("Сведения",), ())
    return Table(key, title, tuple(str(LABELS.get(c, c)) for c in columns), tuple(tuple(scalar(r.get(c), c) for c in columns) for r in rows))


def profile_tables(profiles):
    main, quality, activity, counterparties, dynamics = {}, {}, {}, {}, {}
    for i, profile in enumerate(profiles):
        metrics = profile["metrics"]
        group = f"{profile['metadata'].get('windowDays', '?')}д / {profile['metadata'].get('currency', '')} / {profile['metadata'].get('direction', '')} / {i + 1}"
        excluded = {"quality", "observation", "exclusions", "feedbackReference", "freshness", "dailyCounts", "hourCountsUtc", "weekdayShareUtc", "counterparties", "knownCounterparties", "dynamics", "units"}
        scalars = {k: v for k, v in metrics.items() if k not in excluded and not isinstance(v, (dict, list))}
        for part in ("baseline", "observed"):
            scalars[part] = {k: v for k, v in metrics.get(part, {}).items() if not isinstance(v, (dict, list))}
        main[group] = dict(metadata=profile["metadata"], metrics=scalars, usage=profile.get("reportUsage"))
        quality[group] = {k: metrics[k] for k in ("quality", "observation", "exclusions", "feedbackReference", "dataStatus", "confidence") if k in metrics}
        quality[group]["servedAt"] = profile.get("servedAt")
        activity[group] = {k: metrics[k] for k in ("dailyCounts", "hourCountsUtc", "hourShareUtc", "weekdayShareUtc", "weekdayCountsUtc") if k in metrics}
        counterparties[group] = {k: metrics[k] for k in ("counterparties", "knownCounterparties") if k in metrics}
        dynamics[group] = metrics.get("dynamics")
    currencies = {p["metadata"].get("currency", "") for p in profiles}
    currency = next(iter(currencies)) if len(currencies) == 1 else ""
    return [fields("profile", "Профиль", main, currency), fields("quality", "Качество", quality), fields("activity", "Временная активность UTC", activity), fields("counterparties", "Контрагенты", counterparties), fields("dynamics", "Динамика", dynamics)]


def make_tables(report):
    snapshot = report["snapshot"]
    if len(canonical(snapshot)) > MAX_BYTES or snapshot["reportType"] not in NAMES:
        raise DomainError("BAD_INPUT", "Снимок не поддерживается или превышает 2 MiB")
    data, kind = snapshot["data"], snapshot["reportType"]
    metadata = dict(reportId=report["id"], snapshotHash=report["snapshotHash"], exportVersion=VERSION,
                    savedAt=report["createdAt"], **{k: v for k, v in snapshot.items() if k != "data"})
    tables = [fields("parameters", "Параметры", metadata)]
    if kind == "PROFILE":
        tables.append(fields("client", "Клиент", data["client"]))
        tables.extend(profile_tables([data["profile"]]))
    elif kind == "OPERATION":
        tables.append(fields("transactions", "Операции", data["transaction"], data["transaction"]["currency"]))
        assessment = data["assessment"]
        omit = {"profiles", "baseline", "checks", "historySnapshot", "rules", "assessmentPolicy", "explanations"}
        tables.append(fields("summary", "Итог оценки", {k: v for k, v in assessment.items() if k not in omit}))
        checks = assessment.get("checks", [])
        tables.append(Table("checks", "Проверки", ("Проверка", "Состояние", "Вклад", "Наблюдение", "База", "Причины"), tuple((c["code"], c["status"], c.get("contribution"), scalar(c.get("observed")), scalar(c.get("baseline")), scalar(c.get("dataReasons"))) for c in checks)))
        tables.append(fields("check_details", "Детали проверок", checks))
        tables.extend(profile_tables(data.get("profiles", [])))
        tables.append(fields("context", "Контекст и политика", {k: assessment.get(k) for k in ("historySnapshot", "rules", "assessmentPolicy", "explanations")}))
        review = data["review"]
        tables.extend([fields("case", "Рассмотрение", dict(case=review.get("case"), statusAxis=review.get("statusAxis"))), records("comments", "Комментарии", review.get("comments", [])), records("decisions", "Решения", review.get("decisions", []))])
    else:
        tx, analysis, case = data["transactions"], data["analysis"], data["cases"]
        tables.extend([fields("summary", "Итоги", dict(transactions={k: v for k, v in tx.items() if k not in ("groups", "cohort")}, analysis={k: v for k, v in analysis.items() if k not in ("resultRefs", "profileRefs")}, cases={k: v for k, v in case.items() if k not in ("caseIds", "decisions")})), records("groups", "Объёмы по валютам", tx["groups"]), records("transactions", "Операции", tx["cohort"]), records("checks", "Проверки и результаты", analysis["resultRefs"]), records("profile", "Профили групп", analysis["profileRefs"]), fields("quality", "Качество", {k: v for k, v in analysis.items() if k not in ("resultRefs", "profileRefs")}), records("decisions", "Решения", case.get("decisions", [])), fields("case_refs", "Ссылки случаев", case.get("caseIds", []))])
    if kind == "PROFILE":
        p, m = data["profile"]["metadata"], data["profile"]["metrics"]
        overview = [("Клиент", data["client"].get("displayName"), ""), ("Окно", p["windowDays"], "дней"), ("Версия профиля", p["version"], ""), ("Направление", p["direction"], ""), ("Предметный срез UTC", p["asOf"], ""), ("Операций в базе", m.get("sampleSize"), ""), ("Наблюдалось", m.get("observedCount"), ""), ("Исключено по решениям", m.get("excludedCount"), ""), ("Объём", scalar(m.get("totalAmount"), "totalAmount"), p["currency"]), ("Медиана", scalar(m.get("medianAmount"), "medianAmount"), p["currency"]), ("MAD", scalar(m.get("madAmount"), "madAmount"), p["currency"])]
    elif kind == "OPERATION":
        tx, a = data["transaction"], data["assessment"]
        score = None if a.get("assessmentStatus") == "UNAVAILABLE" else a.get("assessmentScore") if a.get("assessmentScore") is not None else a.get("score")
        overview = [("Клиент", tx.get("clientName"), ""), ("Операция", tx["id"], ""), ("Сумма", scalar(tx["amount"], "amount"), tx["currency"]), ("Направление", tx["direction"], ""), ("Результат", a["resultId"], ""), ("Запуск", a["runId"], ""), ("Время расчёта UTC", a.get("calculatedAt"), ""), ("Оценка", score, "баллы"), ("Порог", a.get("threshold"), "баллы"), ("Полнота", a.get("assessmentStatus", "LEGACY"), "")]
    else:
        tx, a, c = data["transactions"], data["analysis"], data["cases"]
        overview = [("Операций в периоде", tx["transactionCount"], ""), ("Уникальных клиентов", tx["uniqueClients"], ""), ("Операций с результатом", a["resultCount"], ""), ("С доступной оценкой", a["usableAssessmentCount"], ""), ("Случаев подборки", c["caseCount"], ""), ("Решений в периоде", c["decisionCount"], "")]
    parameters, *details = tables
    tables = [Table("overview", "Сводка", ("Показатель", "Значение", "Единица"), tuple(overview)), *details, parameters]
    if sum(len(t.rows) for t in tables) > 20000:
        raise DomainError("BAD_INPUT", "Экспорт превышает 20000 строк; сузьте отчёт")
    for table in tables:
        for row in table.rows:
            for value in row:
                if isinstance(value, str) and (len(value.encode("utf-16-le")) > 65534 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)):
                    raise DomainError("BAD_INPUT", "Текст слишком длинный или содержит управляющие символы")
    return tables
