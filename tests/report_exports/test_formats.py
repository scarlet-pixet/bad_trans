from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[2]
def saved(kind):return json.loads((ROOT/'tests/report_exports/fixtures'/f'{kind}.json').read_text(encoding='utf-8'))
def test_pdf_contains_cyrillic_and_exact_original_snapshot():
    from backend.reporting.exporters import render_export
    from backend.common.report_contract import canonical
    from pypdf import PdfReader
    from io import BytesIO
    report=saved('profile');content,mime,suffix=render_export(report,'PDF')
    reader=PdfReader(BytesIO(content))
    assert mime=='application/pdf' and suffix=='.pdf'
    assert 'Профиль клиента' in ''.join(p.extract_text() for p in reader.pages)
    assert reader.attachments['snapshot.json'][0]==canonical(report['snapshot'])


from io import BytesIO
from zipfile import ZipFile
import csv
import xml.etree.ElementTree as ET
from decimal import Decimal
import copy
import pytest
from backend.reporting.exporters import render_export, exact_excel_number, csv_text
from backend.reporting.tables import make_tables
from backend.common.report_contract import canonical, digest
from backend.common.data import DomainError

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook(content):
    with ZipFile(BytesIO(content)) as z:
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',NS)]
        sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('m:sheets/m:sheet',NS)
        result={}
        for i,sheet in enumerate(sheets,1):
            xml=ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml'));rows=[]
            assert not xml.findall('.//m:f',NS) and not xml.findall('.//m:hyperlink',NS)
            for row in xml.findall('m:sheetData/m:row',NS):
                data=[]
                for cell in row.findall('m:c',NS):
                    value=cell.find('m:v',NS);text=value.text if value is not None else None
                    data.append((cell.attrib['r'],strings[int(text)] if text is not None and cell.attrib.get('t')=='s' else text,cell.attrib.get('t')))
                rows.append(data)
            result[sheet.attrib['name']]=(rows,xml)
        return result


@pytest.mark.parametrize('kind',['profile','operation','period'])
def test_all_formats_share_saved_source_tables_and_repeatable_bytes(kind):
    r=saved(kind);before=copy.deepcopy(r);tables=make_tables(r)
    pdf=render_export(r,'PDF')[0];xlsx=render_export(r,'XLSX')[0];bundle=render_export(r,'CSV')[0]
    assert pdf==render_export(r,'PDF')[0]
    assert xlsx==render_export(r,'XLSX')[0] and bundle==render_export(r,'CSV')[0]
    from pypdf import PdfReader
    reader=PdfReader(BytesIO(pdf));assert reader.attachments['snapshot.json'][0]==canonical(r['snapshot'])
    text=''.join(p.extract_text() for p in reader.pages)
    assert 'Страница 1' in text and r['snapshotHash'] in text
    book=workbook(xlsx)
    assert 'Параметры' in book and ('Профиль' in book or 'Профили групп' in book)
    assert 'Качество' in book
    if kind!='profile':assert 'Операции' in book and any('Проверки' in k for k in book)
    source=''.join(row[1][1] for row in book['JSON'][0][1:])
    assert source==canonical(r['snapshot']).decode('utf-8')
    with ZipFile(BytesIO(bundle)) as z:
        assert z.read('snapshot.json')==canonical(r['snapshot'])
        manifest=__import__('json').loads(z.read('manifest.json'))
        assert manifest['snapshotHash']==r['snapshotHash']
        for t in tables:
            raw=z.read(t.key+'.csv');assert raw.startswith(b'\xef\xbb\xbf')
            rows=list(csv.reader(__import__('io').StringIO(raw.decode('utf-8-sig')),delimiter=';'))
            assert rows[0][2:]==list(t.headers) and len(rows)-1==len(t.rows)
            xrows=book[t.title[:31]][0]
            assert len(xrows)==4+len(t.rows) # title,meta,note,header + body
            for actual,expected in zip(rows[1:],t.rows):
                assert actual[2:]==[csv_text(v) for v in expected]
            assert book[t.title[:31]][1].find('m:autoFilter',NS) is not None
            assert book[t.title[:31]][1].find('m:sheetViews/m:sheetView/m:pane',NS) is not None
    assert r==before


def test_money_precision_and_formula_text_are_preserved():
    r=saved('period');groups=r['snapshot']['data']['transactions']['groups']
    groups[0]['amount']='9999999999999999.99';groups[0]['direction']='=HYPERLINK("bad")'
    r['snapshotHash']=digest(r['snapshot'])
    book=workbook(render_export(r,'XLSX')[0]);cells=[c for row in book['Объёмы по валютам'][0] for c in row]
    assert any(v=='9999999999999999.99' and t=='s' for _,v,t in cells)
    assert any(v=='=HYPERLINK("bad")' and t=='s' for _,v,t in cells)
    assert any(v=='7' and t is None for _,v,t in cells)
    raw=render_export(r,'CSV','groups')[0].decode('utf-8-sig')
    rows=list(csv.reader(__import__('io').StringIO(raw),delimiter=';'))
    assert '9999999999999999.99' in rows[1] and any(v.startswith("'=") for v in rows[1])
    assert csv_text(Decimal('-10.25'))=='-10.25' and csv_text('  +cmd')=="'  +cmd"
    assert not exact_excel_number(Decimal('1234567890.123456')) and exact_excel_number(Decimal('123.25'))


def test_format_and_size_limits_reject_without_truncation():
    r=saved('period')
    for fmt,table in [('DOCX',None),('PDF','groups'),('CSV','unknown')]:
        with pytest.raises(DomainError):render_export(r,fmt,table)
    r['snapshot']['data']['transactions']['transactionCount']=501
    with pytest.raises(DomainError) as e:render_export(r,'PDF')
    assert '500' in str(e.value)


def test_long_cyrillic_name_and_comment_paginate_without_loss(tmp_path):
    r=saved('operation');name='Клиент с длинным именем '+('Александрович '*30)
    r['snapshot']['data']['transaction']['clientName']=name
    review=r['snapshot']['data']['review'];review['case']={'status':'IN_PROGRESS'}
    comment='Длинное пояснение <>& '+' '.join(f'Фрагмент{i:04d}' for i in range(350))
    review['comments']=[{'author_id':'staff','text':comment,'created_at':r['createdAt']}]
    r['snapshotHash']=digest(r['snapshot'])
    content=render_export(r,'PDF')[0]
    from pypdf import PdfReader
    reader=PdfReader(BytesIO(content));text=''.join(p.extract_text() for p in reader.pages)
    assert len(reader.pages)>1 and 'Длинное пояснение <>&' in text
    assert all(f'Фрагмент{i:04d}' in text for i in range(350))
    assert reader.attachments['snapshot.json'][0]==canonical(r['snapshot'])
