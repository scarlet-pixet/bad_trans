"""CSV preview and the atomic Transaction import command."""

import base64
import csv
from hashlib import sha256
from io import StringIO

import streamlit as st

from backend.common.data import DomainError
from backend.common.import_csv import MAX_BYTES, MAX_ROWS, OPTIONAL, REQUIRED, SAMPLE, normalize_transaction


def parse_csv(raw):
    if len(raw) > 256_000:
        raise ValueError("Размер файла не должен превышать 256 КБ.")
    try:
        source = StringIO(raw.decode("utf-8-sig"), newline="")
    except UnicodeDecodeError:
        raise ValueError("Сохраните CSV в кодировке UTF-8.") from None
    header_line = source.readline()
    source.seek(0)
    reader = csv.reader(source, delimiter=";" if ";" in header_line else ",", strict=True)
    file_hash = sha256(raw).hexdigest()
    items, lines, errors = [], [], []
    line = 1
    try:
        header = [name.strip() for name in next(reader, [])]
        if len(header) != len(set(header)) or not set(REQUIRED) <= set(header) <= {*REQUIRED, OPTIONAL}:
            raise ValueError("Колонки CSV должны совпадать с образцом. counterpartyId необязательна.")
        while True:
            line = reader.line_num + 1
            fields = next(reader, None)
            if fields is None:
                break
            if not fields:
                continue
            if len(items) + len(errors) >= 100:
                raise ValueError("Один файл может содержать до 100 операций.")
            try:
                if len(fields) != len(header):
                    raise ValueError("Число полей не совпадает с заголовком.")
                items.append(normalize_transaction(dict(zip(header, fields)), file_hash, line))
                lines.append(line)
            except (ValueError, DomainError) as exc:
                errors.append({"Строка файла": line, "Причина": str(exc)})
    except csv.Error as exc:
        raise ValueError(f"Строка {line}: неверный формат CSV ({exc}).") from None
    if not items and not errors:
        raise ValueError("Файл не содержит операций.")
    return file_hash, items, lines, errors


def render_import(call):
    st.caption("Загрузите CSV UTF-8. Замените accountId из образца на ID открытого счёта с нужной валютой.")
    st.download_button("Скачать образец CSV", SAMPLE, "transactions.csv", mime="text/csv")
    mode = st.radio("Режим импорта", ["Малый пакет", "Фоновая загрузка"], horizontal=True)
    if mode == "Фоновая загрузка":
        render_bulk(call)
        return
    upload = st.file_uploader("Файл операций", type=["csv"])
    if upload is None:
        return
    try:
        file_hash, items, lines, errors = parse_csv(upload.getvalue())
    except ValueError as exc:
        st.error(str(exc))
        return
    if items:
        st.dataframe([{"Строка файла": line, **{k: v for k, v in item.items() if k != "idempotencyKey"}}
                      for line, item in zip(lines, items)], hide_index=True)
    if errors:
        st.error("Исправьте ошибки в файле перед отправкой.")
        st.dataframe(errors, hide_index=True)
        return
    st.caption("Ошибка любой строки отклоняет весь пакет. Совпадающие операции считаются дубликатами.")
    state_key = f"import-result:{file_hash}"
    if st.button("Импортировать пакет"):
        value = call(
            "mutation($input:ImportBatchInput!){importTransactions(input:$input)"
            "{batchId status receivedCount newCount duplicateCount errors{row code message}}}",
            dict(input=dict(fileHash=file_hash, items=items, idempotencyKey=f"csv:{file_hash}")),
        )
        if value is not None:
            st.session_state[state_key] = value["importTransactions"]
    result = st.session_state.get(state_key)
    if result:
        render_status = st.success if result["status"] == "ACCEPTED" else st.error
        render_status("Пакет принят" if result["status"] == "ACCEPTED" else "Пакет отклонён")
        st.code(result["batchId"])
        st.write(f"Получено — {result['receivedCount']}, добавлено — {result['newCount']}, "
                 f"дубликатов — {result['duplicateCount']}.")
        if result["errors"]:
            st.dataframe([
                {"Строка файла": lines[row["row"] - 1], "Код": row["code"], "Причина": row["message"]}
                for row in result["errors"]
            ], hide_index=True)


def render_bulk(call):
    st.caption(f"До {MAX_ROWS:,} операций и 2 МиБ. Сервер разбирает файл перед постановкой "
               "в очередь. После запуска принятые порции сохраняются, даже если другие строки отклонены.")
    upload = st.file_uploader("Файл фонового импорта", type=["csv"], max_upload_size=2)
    if upload is not None:
        raw = upload.getvalue()
        if len(raw) > MAX_BYTES:
            st.error("Файл превышает 2 МиБ.")
        elif st.button("Проверить и поставить в очередь", type="primary"):
            fingerprint = sha256(raw).hexdigest()
            data = call(
                "mutation($data:String!,$hash:String!,$key:String!){startBulkImport("
                "fileBase64:$data,fileHash:$hash,idempotencyKey:$key)}",
                dict(data=base64.b64encode(raw).decode("ascii"), hash=fingerprint, key=f"csv:{fingerprint}"),
            )
            if data is not None:
                st.session_state["bulk_import_lookup"] = data["startBulkImport"]["id"]
                st.success("Задача сохранена.")
            else:
                st.caption("При сбое связи можно отправить тот же файл ещё раз.")
    if st.button("Обновить задачи"):
        st.rerun()
    offset = st.session_state.get("bulk_import_offset", 0)
    result = call("query($offset:Int!){bulkImports(offset:$offset)}", dict(offset=offset))
    jobs = result["bulkImports"] if result else {"items": [], "nextOffset": None}
    entries = {entry["id"]: entry for entry in jobs["items"]}
    selection = st.selectbox("Мои задачи импорта", [None, *entries], format_func=lambda item: (
        f"{entries[item]['createdAt'][:19]} UTC · {entries[item]['status']} · {item}" if item else "Выбрать"
    ))
    previous, following = st.columns(2)
    if previous.button("Предыдущие задачи", disabled=not offset):
        st.session_state["bulk_import_offset"] = max(0, offset - 20)
        st.rerun()
    if following.button("Следующие задачи", disabled=jobs["nextOffset"] is None):
        st.session_state["bulk_import_offset"] = jobs["nextOffset"]
        st.rerun()
    direct_id = st.text_input("ID задачи импорта", key="bulk_import_lookup").strip()
    identifier = direct_id or selection
    if not identifier:
        return
    data = call("query($id:ID!){bulkImport(id:$id)}", dict(id=identifier))
    if data is None:
        return
    job = data["bulkImport"]
    status = {"QUEUED": "В очереди", "RUNNING": "В работе", "COMPLETED": "Завершён",
              "PARTIAL": "Завершён частично", "FAILED": "Все строки отклонены"}
    st.write(f"Состояние — {status[job['status']]}")
    st.code(job["id"])
    st.progress((job["received"]-job["pending"]) / job["received"])
    st.write(f"Получено — {job['received']}, ожидают — {job['pending']}, добавлено — {job['added']}, "
             f"дубликатов — {job['duplicates']}, ошибок — {job['failed']}.")
    if job["errors"]:
        st.dataframe(job["errors"], hide_index=True)
    if job["failed"] and job["status"] in ("PARTIAL", "FAILED"):
        key = f"bulk_import_errors:{identifier}"
        if st.button("Подготовить файл ошибок"):
            result = call("query($id:ID!){bulkImportErrors(id:$id)}", dict(id=identifier))
            if result:
                st.session_state[key] = result["bulkImportErrors"]
        if key in st.session_state:
            st.download_button("Скачать ошибки CSV", "\ufeff" + st.session_state[key],
                               f"import-errors-{identifier}.csv", mime="text/csv")
