from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"


def test_tabs_and_completed_run_state_survive_streamlit_rerun():
    at = AppTest.from_file(str(APP))
    at.run(timeout=15)
    assert not at.exception
    assert len(at.tabs) == 7
    assert not at.radio
    assert at.button[0].label == "RUN BI-WEEKLY NEWSLETTER"
    assert any("SERPAPI_API_KEY" in error.value for error in at.error)
    at.run(timeout=15)
    assert not at.exception
    assert len(at.tabs) == 7
