"""Deterministic server-side PDF/XLSX/CSV export from an authorized saved report."""

import csv
from datetime import datetime, UTC
from decimal import Decimal
from functools import partial
from io import BytesIO, StringIO
from pathlib import Path
import re
import threading
import math
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, CondPageBreak
import xlsxwriter

from backend.common.data import DomainError
from backend.common.report_contract import canonical
from backend.reporting.tables import make_tables, VERSION, NAMES

FONT = "BadTransSans"
_FONT_LOCK = threading.Lock()
_EXPORT_SLOTS = threading.BoundedSemaphore(2)
MAX_FILE_BYTES = 2 * 1024 * 1024


class PdfPageLimit(RuntimeError):
    """ReportLab may reconstruct exceptions with a single annotated message."""


def display(value):
    if value is None:
        return "Нет данных"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def exact_excel_number(value):
    """Excel has 15 significant decimal digits; preserve anything beyond as text."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    d = Decimal(str(value))
    return d.is_finite() and len(d.normalize().as_tuple().digits) <= 15 and (d == 0 or -300 <= d.adjusted() <= 300)


def csv_text(value):
    if value is None:
        return ""
    if isinstance(value, str) and (re.match(r"^[\s\ufeff]*[=+\-@]", value) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return display(value)


def _zip(files):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o600 << 16
            z.writestr(entry, data)
    return output.getvalue()


def _csv(report, table):
    stream = StringIO(newline="")
    writer = csv.writer(stream, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Отчёт ID", "SHA256 снимка", *[csv_text(h) for h in table.headers]])
    for row in table.rows:
        writer.writerow([report["id"], report["snapshotHash"], *[csv_text(v) for v in row]])
    return stream.getvalue().encode("utf-8-sig")


def _xlsx(report, tables):
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False})
    when = datetime.fromisoformat(report["snapshot"]["assembledAt"].replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
    workbook.set_properties({"title": NAMES[report["snapshot"]["reportType"]], "author": report["snapshot"]["author"]["login"], "created": when, "comments": VERSION})
    title = workbook.add_format({"bold": True, "font_size": 14, "font_color": "#123253"})
    header = workbook.add_format({"bold": True, "font_size": 10, "bg_color": "#E6EDF5", "font_color": "#123253", "text_wrap": True, "valign": "vcenter", "border": 1, "border_color": "#CFD8E4"})
    body = workbook.add_format({"font_size": 10, "valign": "top", "text_wrap": True})
    number = workbook.add_format({"font_size": 10, "valign": "top", "num_format": "General"})
    exact = workbook.add_format({"font_size": 10, "valign": "top", "text_wrap": True, "bg_color": "#FFF2DC"})
    used = set()
    for table in tables:
        name = table.title[:31]
        if name in used:
            raise DomainError("DATA_ACCESS_ERROR", "Повтор названия листа экспорта")
        used.add(name)
        sheet = workbook.add_worksheet(name)
        sheet.hide_gridlines(2)
        sheet.set_tab_color("#123253" if table.key in ("overview", "summary", "profile", "transactions") else "#91A8C1")
        last_col = max(1, len(table.headers) - 1)
        sheet.merge_range(1, 0, 1, last_col, table.title, title)
        sheet.merge_range(2, 0, 2, last_col, f"Снимок {report['id']} | {report['snapshotHash']}", body)
        sheet.merge_range(3, 0, 3, last_col, "UTC. Значения свыше 15 значащих цифр сохранены текстом с выделением. Полный источник — лист JSON.", body)
        sheet.set_row(2, 32)
        sheet.set_row(3, 32)
        widths = [100 if label in ("Комментарий", "Основание") else 80 if label == "Значение" else 45 if label == "Поле" else 26 for label in table.headers]
        for col, label in enumerate(table.headers):
            sheet.write_string(5, col, label, header)
            sheet.set_column(col, col, widths[col])
        sheet.set_row(5, 30)
        for row_no, row in enumerate(table.rows, 6):
            lines = 1
            for col, value in enumerate(row):
                if value is None or value == "":
                    sheet.write_blank(row_no, col, None, body)
                elif isinstance(value, bool):
                    sheet.write_boolean(row_no, col, value, body)
                elif exact_excel_number(value):
                    sheet.write_number(row_no, col, float(value), number)
                else:
                    is_number = isinstance(value, (Decimal, int, float))
                    text = str(value)
                    sheet.write_string(row_no, col, text, exact if is_number else body)
                    if is_number:
                        sheet.write_comment(row_no, col, "Точное исходное значение сохранено текстом: ограничение15значащих цифр Excel.")
                    lines = max(lines, sum(max(1, math.ceil(len(line) / (widths[col] - 2))) for line in text.splitlines()) or 1)
            sheet.set_row(row_no, min(409, max(20, lines * 14)))
        sheet.freeze_panes(6, 0)
        sheet.autofilter(5, 0, max(5, 5 + len(table.rows)), len(table.headers) - 1)
        sheet.repeat_rows(5)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.set_footer("&L" + VERSION + "&RСтраница &P")
    sheet = workbook.add_worksheet("JSON")
    sheet.hide_gridlines(2)
    sheet.write_row(0, 0, ["Часть", "Canonical snapshot.json (соединить по порядку без разделителя)"], header)
    raw = canonical(report["snapshot"]).decode("utf-8")
    for i, start in enumerate(range(0, len(raw), 15000), 1):
        sheet.write_number(i, 0, i)
        sheet.write_string(i, 1, raw[start:start + 15000])
    sheet.set_column(0, 0, 10)
    sheet.set_column(1, 1, 100)
    sheet.freeze_panes(1, 0)
    workbook.close()
    # Zip entry timestamps must not depend on the time of this download.
    with ZipFile(BytesIO(output.getvalue())) as z:
        return _zip({n: z.read(n) for n in z.namelist()})


def _pdf(report, tables):
    snapshot = report["snapshot"]
    if snapshot["reportType"] == "PERIOD" and snapshot["data"]["transactions"]["transactionCount"] > 500:
        raise DomainError("BAD_INPUT", "PDF ограничен500операциями; сузьте отчёт или выберите XLSX/CSV")
    with _FONT_LOCK:
        if FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT, str(Path(__file__).parents[1] / "assets/fonts/DejaVuSans.ttf")))
    output = BytesIO()
    page = A4 if snapshot["reportType"] == "PROFILE" else landscape(A4)
    styles = {"body": ParagraphStyle("body", fontName=FONT, fontSize=8.2, leading=10.5, splitLongWords=True, alignment=TA_LEFT),
              "title": ParagraphStyle("title", fontName=FONT, fontSize=16, leading=20, spaceAfter=10),
              "heading": ParagraphStyle("heading", fontName=FONT, fontSize=12, leading=15, spaceBefore=12, spaceAfter=7, keepWithNext=False)}
    doc = SimpleDocTemplate(output, pagesize=page, rightMargin=32, leftMargin=32, topMargin=32, bottomMargin=35, title=NAMES[snapshot["reportType"]], author=snapshot["author"]["login"], pageCompression=1, invariant=1)
    def paragraph(value):
        return Paragraph(escape(display(value)).replace("\n", "<br/>"), styles["body"])
    story = [Paragraph(NAMES[snapshot["reportType"]], styles["title"]), paragraph(f"Отчёт {report['id']} | автор {snapshot['author']['login']} | сохранён {report['createdAt']}"), paragraph(f"Срез UTC {snapshot['cutoff']} | SHA256 {report['snapshotHash']}"), paragraph("Сохранённый снимок. Полный canonical snapshot.json включён во вложение PDF. Срезы владельцев раздельны, общей атомарной точки нет."), Spacer(1, 8)]
    for table in tables:
        story.append(CondPageBreak(90))
        story.append(Paragraph(escape(table.title), styles["heading"]))
        if not table.rows:
            story.append(paragraph("Нет записей на сохранённом срезе."))
            continue
        weights = [2.2 if h in ("Поле", "Значение", "Комментарий", "Основание", "Наблюдение", "База") else 1 for h in table.headers]
        widths = [(page[0] - 64) * w / sum(weights) for w in weights]
        cells = [[paragraph(h) for h in table.headers], *[[paragraph(v) for v in row] for row in table.rows]]
        grid = LongTable(cells, colWidths=widths, repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
        grid.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6EDF5")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#123253")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#B4C3D4"))]))
        story.append(grid)

    def footer(canvas, document):
        if document.page > 120:
            raise PdfPageLimit("PDF exceeds120pages")
        canvas.setFont(FONT, 8)
        canvas.drawString(32, 19, VERSION + " | " + report["id"][:8])
        canvas.drawRightString(page[0] - 32, 19, f"Страница {document.page}")

    try:
        doc.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=partial(Canvas, invariant=1))
    except PdfPageLimit:
        raise DomainError("BAD_INPUT", "PDF превышает120страниц; сузьте отчёт") from None
    writer = PdfWriter(clone_from=PdfReader(BytesIO(output.getvalue())))
    writer.add_attachment("snapshot.json", canonical(snapshot))
    final = BytesIO()
    writer.write(final)
    return final.getvalue()


def _render_export(report, format, table=None):
    if format not in ("PDF", "XLSX", "CSV") or (table is not None and format != "CSV"):
        raise DomainError("BAD_INPUT", "Формат: PDF, XLSX или CSV; таблица применима только к CSV")
    tables = make_tables(report)
    if format == "PDF":
        data, mime, suffix = _pdf(report, tables), "application/pdf", ".pdf"
    elif format == "XLSX":
        data, mime, suffix = _xlsx(report, tables), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
    elif table is not None:
        chosen = next((t for t in tables if t.key == table), None)
        if chosen is None:
            raise DomainError("BAD_INPUT", "Неизвестная таблица CSV")
        data, mime, suffix = _csv(report, chosen), "text/csv; charset=utf-8", "-" + chosen.key + ".csv"
    else:
        files = {t.key + ".csv": _csv(report, t) for t in tables}
        files["snapshot.json"] = canonical(report["snapshot"])
        files["manifest.json"] = canonical(dict(version=VERSION, reportId=report["id"], snapshotHash=report["snapshotHash"], encoding="UTF-8 BOM", delimiter=";", textFormulaEscape="apostrophe prefix; numeric negatives preserved", tables=[dict(key=t.key, title=t.title, rows=len(t.rows)) for t in tables]))
        data, mime, suffix = _zip(files), "application/zip", "-csv.zip"
    if len(data) > MAX_FILE_BYTES:
        raise DomainError("BAD_INPUT", "Файл превышает2MiB; сузьте отчёт")
    return data, mime, suffix


def render_export(report, format, table=None):
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise DomainError("DEPENDENCY_UNAVAILABLE", "Экспорт занят; повторите запрос позже")
    try:
        return _render_export(report, format, table)
    finally:
        _EXPORT_SLOTS.release()
