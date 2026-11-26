from streamlit.testing.v1 import AppTest

CALLS=[]
STATE="COMPLETED"
IDENTIFIER="00000000-0000-0000-0000-000000000001"


def fake_api(query,args=None):
    args=args or {}
    if 'startProfileRun' in query:
        CALLS.append(args)
        return {'startProfileRun':{'id':IDENTIFIER}}
    if 'profileRuns' in query:return {'profileRuns':[]}
    if 'profileRun' in query:
        return {'profileRun':dict(status=STATE,selection={'period':{}},
            context=dict(asOf='2026-11-01T00:00:00Z',ingestedBefore='2026-11-17T00:00:00Z',feedbackReference={'revision':0}),
            selectionSnapshot=None,totalGroups=0,completedGroups=0,failedGroups=0,skippedGroups=0,
            selectedClients=0,completedClients=0,selectionComplete=True,error=None,groups=[],nextOffset=None)}
    raise AssertionError(query)


def app():
    return AppTest.from_string('from frontend.profile_runs import render_profile_runs\nfrom tests.profile_metrics.test_bulk_runs_ui import fake_api\nrender_profile_runs(fake_api)',default_timeout=30).run()


def click(at,label):
    next(x for x in at.button if x.label==label).click().run()
    assert not at.exception


def test_double_submit_keeps_key_new_command_changes_key_and_link_is_saved():
    CALLS.clear()
    at=app();assert not at.exception
    click(at,'Запустить формирование');click(at,'Запустить формирование')
    assert CALLS[0]['k']==CALLS[1]['k']
    assert at.query_params['profileRun']==IDENTIFIER
    click(at,'Новая команда с теми же параметрами');click(at,'Запустить формирование')
    assert CALLS[2]['k']!=CALLS[0]['k']


def test_empty_run_is_explained_without_invented_versions():
    global STATE
    STATE='EMPTY'
    try:
        at=app();click(at,'Запустить формирование')
        assert any('не найдено' in x.value for x in at.info)
    finally:STATE='COMPLETED'
