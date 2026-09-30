"""WhatsApp triage keeps up with a room full of seminar QR scans.

Survey requests never reach the LLM, so they must not pay the pause that paces
LLM calls, and batches are handled in the order they arrived rather than in
random batch-id order. These tests exec the real ``triage_agent.py`` with its
deployment root pointed at a temp dir.
"""

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIAGE = REPO_ROOT / "custom" / "whatsapp" / "triage_agent.py"
QR_TEXT = "I want to do the survey and get the presentation link."


class _StopLoop(BaseException):
    """Breaks out of ``while True`` past the loop's ``except Exception``."""


@pytest.fixture
def triage(tmp_path, monkeypatch):
    root = tmp_path / "whatsapp-messages"
    root.mkdir()
    (root / "config.json").write_text(json.dumps({}))
    credit = types.ModuleType("track_credit_helper")
    credit.__dict__["track_inference"] = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "track_credit_helper", credit)
    source = TRIAGE.read_text().replace("/opt/data/whatsapp-messages", str(root))
    spec = importlib.util.spec_from_loader("_test_wa_triage", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(TRIAGE)
    exec(compile(source, str(TRIAGE), "exec"), module.__dict__)
    return module


def _batch(triage, name, text, source="phone2", mtime=None):
    path = Path(triage.BATCH_DIR) / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "sender_phone": f"+{name}",
                "source_phone": source,
                "message_count": 1,
                "messages": [
                    {"msg_id": name, "text": text, "chat_id": f"{name}@lid", "is_group": False}
                ],
            }
        )
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return str(path)


def test_batches_run_in_arrival_order_and_only_llm_calls_pause(triage, monkeypatch):
    late_llm = _batch(triage, "aaa", "hello", mtime=3_000)
    first_survey = _batch(triage, "zzz", QR_TEXT, mtime=1_000)
    second_survey = _batch(triage, "mmm", QR_TEXT, mtime=2_000)
    order = []
    sleeps = []

    def fake_process(path):
        order.append(path)
        return path == late_llm

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if seconds == 3:
            raise _StopLoop

    monkeypatch.setattr(triage, "process_batch_file", fake_process)
    monkeypatch.setattr(triage.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        triage.main()
    assert order == [first_survey, second_survey, late_llm]
    assert sleeps == [1, 3]


def test_survey_batch_skips_the_llm(triage, monkeypatch):
    recorded = []
    monkeypatch.setattr(
        triage, "record_survey_request", lambda message: recorded.append(message) or True
    )
    monkeypatch.setattr(
        triage, "triage_batch", lambda batch: pytest.fail("survey request reached the LLM")
    )
    path = _batch(triage, "111", QR_TEXT)
    assert triage.process_batch_file(path) is False
    assert [m["chat_id"] for m in recorded] == ["111@lid"]
    assert Path(triage.PROCESSED_DIR, "111.json").exists()


def test_ordinary_batch_reports_its_llm_call(triage, monkeypatch):
    monkeypatch.setattr(triage, "triage_batch", lambda batch: {"tasks": [], "notes": []})
    monkeypatch.setattr(triage, "process_triage_result", lambda batch, result: None)
    path = _batch(triage, "222", "Are we still on for Friday?")
    assert triage.process_batch_file(path) is True
    assert Path(triage.PROCESSED_DIR, "222.json").exists()
