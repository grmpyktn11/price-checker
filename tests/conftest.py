import pytest
from dotenv import load_dotenv

# live tests make real calls and need the real keys. modules read their keys into constants
# at import, so this has to run before anything under backend is imported
load_dotenv()

from backend.services import email  # noqa: E402

# the app is live-only: scrapers always hit the network and every model call is real, so any
# test that runs the pipeline is marked live. what is left to neutralize is email, which no
# test may send, and which is the one source with an offline path of its own.
@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    # the render path still runs, the send returns False
    monkeypatch.setattr(email, "RESEND_API_KEY", "")
    monkeypatch.setattr(email, "USER_EMAIL", "")
