from pathlib import Path
from datetime import date

from streamlit.testing.v1 import AppTest

try:
    from bnl_research_automation.pipeline import new_run_state
except ModuleNotFoundError:
    from pipeline import new_run_state


APP = Path(__file__).parents[1] / "app.py"


def test_tabs_and_completed_run_state_survive_streamlit_rerun():
    at = AppTest.from_file(str(APP))
    at.run(timeout=15)
    assert not at.exception
    assert len(at.tabs) == 8
    assert "Workflow 3: Grok Live Web Search (Experimental)" in at.radio[0].options
    state = new_run_state(date(2026, 9, 23), date(2026, 10, 7), 50)
    state["run_status"] = "COMPLETE"
    at.session_state.run = state
    at.run(timeout=15)
    assert not at.exception
    assert at.session_state.run["run_status"] == "COMPLETE"
    assert len(at.tabs) == 8
