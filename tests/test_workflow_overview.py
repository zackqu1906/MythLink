"""Saved-data navigation must never generate a recommendation or count exposure."""
import json
from pathlib import Path
from urllib.request import urlopen

from test_recommendation_execution import catalog, displayed
from proximic_ring.agent_workflow.reporting.overview import export_overview

import pytest
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')


def test_empty_overview_does_not_create_business_databases(tmp_path):
    root=tmp_path/'empty'
    page=export_overview(root)
    assert page.is_file() and '尚未生成' in page.read_text()
    assert not list(root.rglob('*.sqlite3'))
    assert json.loads((root/'overview.json').read_text())['requests']==0


def test_saved_ranking_feedback_and_execution_export_without_mutating_source(catalog):
    from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution
    result,item,exposure=displayed(catalog)
    prepare_execution(catalog,exposure,{'title':'课堂 <script>bad</script>'})
    before=list(catalog.db.iterdump())
    export_overview(catalog.root)
    assert list(catalog.db.iterdump())==before
    page=(catalog.root/'recommendations/index.html').read_text()
    assert '课堂 &lt;script&gt;bad&lt;/script&gt;' in page and '<script>' not in page
    saved=json.loads((catalog.root/'recommendations'/(result['request_id']+'.json')).read_text())
    assert saved['snapshot']['items']==result['items']
    assert saved['feedback'][0]['kind']=='accepted'
    assert saved['executions'][0]['state']=='prepared'
    assert len(saved['exposures'])==1


def test_live_history_page_refreshes_without_polling_scripts_or_fake_exposures(catalog):
    from proximic_ring.agent_workflow.recording.records_server import RecordsServer
    from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
    server=RecordsServer(catalog.root,on_automatic=lambda _:None)
    try:
        url=server.origin+server.prefix+'overview.html'
        with urlopen(url,timeout=4) as response:assert '全流程总览' in response.read().decode()
        request_recommendations(catalog,surface='native')
        before=list(catalog.db.iterdump())
        with urlopen(server.origin+server.prefix+'recommendations/index.html',timeout=4) as response:
            page=response.read().decode()
        assert '共 1 次已保存请求' in page
        assert '<script' not in page and 'controls.js' not in page
        assert list(catalog.db.iterdump())==before
        assert not catalog.rows('recommendation_exposures')
    finally:server.close();server.thread.join(timeout=3)


def test_corrupt_source_is_marked_and_other_navigation_remains(tmp_path):
    (tmp_path/'operations').mkdir()
    (tmp_path/'operations/catalog.sqlite3').write_text('broken fixture')
    export_overview(tmp_path)
    stats=json.loads((tmp_path/'overview.json').read_text())
    assert stats['warnings'] and '暂不可读取' in (tmp_path/'overview.html').read_text()
    assert (tmp_path/'operations/catalog.sqlite3').read_text()=='broken fixture'
