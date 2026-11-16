"""Native POST download forms check Auth and the owner on every click."""

import html
import os

import streamlit as st
import streamlit.components.v1 as components


def download_buttons(report_id):
    token = st.session_state.get("token")
    if not token:
        return
    base = os.getenv("PUBLIC_GATEWAY_URL", "http://127.0.0.1:18100").rstrip("/")
    action = html.escape(base + "/report-files/" + report_id, quote=True)
    credential = html.escape(token, quote=True)
    forms = []
    for format, label in [("PDF", "Скачать PDF"), ("XLSX", "Скачать XLSX"), ("CSV", "CSV: все таблицы (ZIP)")]:
        forms.append(f'<form action="{action}" method="post"><input type="hidden" name="token" value="{credential}"><input type="hidden" name="format" value="{format}"><button type="submit">{label}</button></form>')
    components.html('<style>body{font:14px sans-serif;margin:0}main{display:flex;gap:10px;flex-wrap:wrap}button{background:#f5f7fb;border:1px solid #bccbdc;border-radius:8px;padding:11px 15px;color:#123253;cursor:pointer}</style><main>' + "".join(forms) + '</main>', height=58, scrolling=False)
