import json
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import pytest
from tests.profile_metrics.test_presentation import profile, NOW


def envelope(kind):
    p=profile()
    exact=dict(metadata={k:p[k] for k in ['id','clientId','currency','direction','version','origin','asOf']},metrics=p,servedAt=NOW.isoformat())
    if kind=='PROFILE':data=dict(client={'displayName':'Report client'},profile=exact)
    elif kind=='OPERATION':data=dict(transaction=dict(id='tx',amount='10.00',currency='BYN',direction='OUT'),assessment=dict(resultId='result',runId='run',score=35,threshold=60,checks=[]),profiles=[dict(exact,metadata=dict(exact['metadata'],windowDays=30))],review=dict(case=None,comments=[],decisions=[]))
    else:data=dict(transactions=dict(transactionCount=4,uniqueClients=2,groups=[dict(currency='BYN',direction='OUT',transactionCount=3,amount='35.00')],cohort=[]),analysis=dict(resultCount=2,usableAssessmentCount=2,denominator=4,profiledGroups=1,profileGroupDenominator=3,readyProfileGroups=0,statuses=dict(LEGACY=2,NO_RESULT=2),refreshJobsByStatus={},eventFailuresAllClients={},axes={},resultRefs=[],profileRefs=[]),cases=dict(caseCount=1,newCasesInPeriod=1,decisionCount=0,decidedCases=0,outcomes={}))
    return dict(id='saved-report',createdAt=NOW.isoformat(),snapshotHash='a'*64,snapshot=dict(reportType=kind,author=dict(login='manager'),cutoff=NOW.isoformat(),assembledAt=NOW.isoformat(),filters={},sourceCuts=[],data=data))


@pytest.mark.parametrize('kind',['PROFILE','OPERATION','PERIOD'])
def test_render_saved_snapshot_and_json_download(kind):
    report=envelope(kind)
    app=AppTest.from_string('from frontend.report_view import render_saved\nrender_saved('+repr(report)+')',default_timeout=20).run()
    assert not app.exception
    assert any('Сохранённый снимок' in item.value for item in app.info)
    assert any('PDF, XLSX' in item.value for item in app.caption)
    if kind=='OPERATION':assert app.metric[0].value=='35'


def test_save_button_sends_selectors_only_and_keeps_retry_key():
    calls=[]
    def api(q,a):calls.append((q,a));return None if len(calls)==1 else dict(createSnapshotReport={'id':'stored'})
    with patch('builtins.report_test_api',api,create=True):
        app=AppTest.from_string('import builtins\nfrom frontend.report_view import save_button\nsave_button(builtins.report_test_api,dict(reportType="PROFILE",profileVersionId="exact"),"Save","save")',default_timeout=20).run()
        app.button[0].click().run();app.button[0].click().run()
    assert not app.exception
    assert calls[0][1]['input']==calls[1][1]['input']
    assert set(calls[0][1]['input'])=={'reportType','profileVersionId','idempotencyKey'}
    assert app.session_state.saved_report_id=='stored'


def test_empty_review_actions_are_explained_not_blank_tables():
    report=envelope('OPERATION')
    report['snapshot']['data']['review']['case']={'status':'CLOSED'}
    report['snapshot']['data']['assessment']['explanations']=[dict(text='Сохранённая причина',contribution=35)]
    app=AppTest.from_string('from frontend.report_view import render_saved\nrender_saved('+repr(report)+')',default_timeout=20).run()
    assert not app.exception
    assert any('Комментариев на срезе нет' in item.value for item in app.info)
    assert any('Решений на срезе нет' in item.value for item in app.info)
    assert any('Сохранённая причина' in item.value for item in app.markdown)
