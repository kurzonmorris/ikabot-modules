"""Auto-Pirate failures must name the stage that broke.

Before this, every failure was reported as:

    Error in:

    Cause:
    <traceback>

— the subject was hardcoded to an empty string, so a notification told you
something had broken but never what. These tests pin the replacement.
"""

import pytest

import ikabot.function.autoPirate as ap
import ikabot.helpers.piratesDecaptcha as pd
import ikabot.helpers.piratesDecaptchaPure as pdp


class FakeSession:
    def getSessionData(self):
        return {}

    def setStatus(self, message):
        pass


@pytest.fixture
def sent(monkeypatch):
    """Collect what would have gone to Telegram/Discord/ntfy."""
    box = []
    monkeypatch.setattr(ap, "sendToBot", lambda s, m, **k: box.append(m))
    monkeypatch.setattr(ap, "_LOCAL_FALLBACK_WARNED", False)
    return box


# ------------------------------------------------------------- stage tags ---

def test_stage_tags_the_failure(sent):
    try:
        with ap._stage("starting the capture mission"):
            raise ConnectionError("server closed the connection")
    except Exception as exc:
        ap._report_failure(FakeSession(), exc)

    assert "Failed while: starting the capture mission" in sent[0]
    assert "ConnectionError: server closed the connection" in sent[0]


def test_innermost_stage_wins():
    """An outer stage must not relabel a failure the inner one already named."""
    with pytest.raises(ap.PirateStageError) as caught:
        with ap._stage("running the pirate loop"):
            with ap._stage("fetching the captcha image"):
                raise ValueError("no image in response")
    assert caught.value.stage == "fetching the captcha image"


def test_untagged_failure_is_reported_honestly(sent):
    """Don't invent a stage we did not record."""
    try:
        raise RuntimeError("something odd")
    except Exception as exc:
        ap._report_failure(FakeSession(), exc)
    assert "an unrecorded step" in sent[0]
    assert "RuntimeError: something odd" in sent[0]


def test_report_includes_the_traceback(sent):
    try:
        with ap._stage("submitting the solved captcha"):
            raise KeyError("captcha")
    except Exception as exc:
        ap._report_failure(FakeSession(), exc)
    assert "Traceback" in sent[0]


# -------------------------------------------------- local solver fallback ---

def test_local_failure_says_why_and_still_solves(sent, monkeypatch):
    monkeypatch.setattr(ap, "LOCAL_DECAPTCHA", True)
    monkeypatch.setattr(ap, "get_captcha_string",
                        lambda p: (_ for _ in ()).throw(MemoryError("no room for weights")))
    monkeypatch.setattr(ap, "getPiratesCaptchaSolution", lambda s, p: "FROMAPI")

    assert ap.resolveCaptcha(FakeSession(), b"x") == "FROMAPI"
    assert "MemoryError: no room for weights" in sent[0]
    assert "remote API" in sent[0]


def test_fallback_is_reported_once_per_run(sent, monkeypatch):
    """It fires inside the solve loop; one message per captcha would bury
    everything else. The log still records each occurrence."""
    monkeypatch.setattr(ap, "LOCAL_DECAPTCHA", True)
    monkeypatch.setattr(ap, "get_captcha_string",
                        lambda p: (_ for _ in ()).throw(MemoryError("no room")))
    monkeypatch.setattr(ap, "getPiratesCaptchaSolution", lambda s, p: "FROMAPI")

    for _ in range(6):
        ap.resolveCaptcha(FakeSession(), b"x")
    assert len(sent) == 1


def test_import_failure_reason_is_preserved(sent, monkeypatch):
    """"onnxruntime missing" and "weights missing" need different fixes, so the
    reason has to survive, not collapse to "unavailable"."""
    monkeypatch.setattr(ap, "LOCAL_DECAPTCHA", False)
    monkeypatch.setattr(ap, "_LOCAL_IMPORT_ERROR",
                        ImportError("No module named 'onnxruntime'"))
    monkeypatch.setattr(ap, "getPiratesCaptchaSolution", lambda s, p: "FROMAPI")

    ap.resolveCaptcha(FakeSession(), b"x")
    assert "ImportError: No module named 'onnxruntime'" in sent[0]


def test_both_solvers_failing_names_the_stage(sent, monkeypatch):
    monkeypatch.setattr(ap, "LOCAL_DECAPTCHA", True)
    monkeypatch.setattr(ap, "get_captcha_string",
                        lambda p: (_ for _ in ()).throw(MemoryError("no room")))
    monkeypatch.setattr(ap, "getPiratesCaptchaSolution",
                        lambda s, p: (_ for _ in ()).throw(ConnectionError("API unreachable")))

    with pytest.raises(ap.PirateStageError) as caught:
        ap.resolveCaptcha(FakeSession(), b"x")
    assert "remote API" in caught.value.stage
    assert isinstance(caught.value.cause, ConnectionError)


# ---------------------------------------------------- solver engine chain ---

def test_both_engines_failing_names_both(monkeypatch):
    monkeypatch.setattr(pd, "_solve_with_onnx",
                        lambda b: (_ for _ in ()).throw(ImportError("No module named 'onnxruntime'")))
    monkeypatch.setattr(pdp, "get_captcha_string",
                        lambda b: (_ for _ in ()).throw(pdp.LocalSolveUnavailable("no memory")))

    with pytest.raises(pd.LocalDecaptchaError) as caught:
        pd.get_captcha_string(b"x")
    text = str(caught.value)
    assert "ONNX: ImportError" in text
    assert "pure: LocalSolveUnavailable" in text


def test_onnx_missing_falls_through_quietly(monkeypatch):
    """A missing ONNX install is normal on ARM/minimal images: log it, don't
    raise, and let the pure solver answer."""
    monkeypatch.setattr(pd, "_solve_with_onnx",
                        lambda b: (_ for _ in ()).throw(ImportError("no onnxruntime")))
    import os
    img = os.path.join(os.path.dirname(__file__), "..", "..", "api", "img", "pirate1.png")
    with open(img, "rb") as f:
        assert pd.get_captcha_string(f.read()) == "QKB24JC"
