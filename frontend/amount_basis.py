"""Display the saved scope and readiness of the amount comparison."""

from decimal import Decimal

import streamlit as st

from frontend.operation_lifecycle import CHANNELS, KINDS


def render_amount_basis(basis):
    scope, value = basis["scope"], basis["value"]
    labels = {
        "GENERAL": "Общий профиль",
        "KIND": "Профиль по виду операции",
        "CHANNEL": "Профиль по каналу",
    }
    detail = KINDS.get(value, value) if scope == "KIND" else CHANNELS.get(value, value)
    title = labels[scope] + (f" · {detail}" if scope != "GENERAL" else "")
    st.write("База сравнения суммы: " + title)
    coverage = basis["generalCoverage"]
    coverage_text = (
        "подтверждено полностью" if coverage["full"]
        else "не подтверждено" if not Decimal(str(coverage["fraction"] or 0))
        else "подтверждено частично"
    )
    st.caption(
        f"{basis['windowDays']} дней · операций: {basis['sampleSize']} · "
        f"активных дней: {basis['activeDays']} · покрытие общего окна {coverage_text}"
    )
    if "knownSampleSize" in basis:
        st.caption(f"С выбранным признаком в общем окне: {basis['knownSampleSize']} операций.")
    for candidate in basis["candidates"]:
        name = labels[candidate["scope"]]
        candidate_value = candidate["value"]
        if candidate_value is not None:
            options = KINDS if candidate["scope"] == "KIND" else CHANNELS
            name += f" ({options.get(candidate_value, candidate_value)})"
        if candidate["reason"] == "NO_SUBJECT_CLASSIFICATION":
            st.caption(f"{name}: источник не указал признак текущей операции.")
        else:
            required = basis["required"]
            st.caption(
                f"{name}: {candidate['sampleSize']} из {required['sampleSize']} требуемых операций, "
                f"{candidate['activeDays']} из {required['activeDays']} активных дней."
            )
    if scope != "GENERAL":
        st.caption("Частота, время, контрагенты и динамика проверяются по общему профилю.")
