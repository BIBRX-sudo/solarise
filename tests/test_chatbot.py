import pytest
from chatbot import get_reply

@pytest.mark.parametrize(
    "message,expected_substring",
    [
        ("hi", "Namaskar! Welcome to Solarise Odisha"),
        ("1", "rooftop solar"),
        ("2", "PM Surya Ghar is a government rooftop solar scheme"),
        ("unknown", "Sorry"),
    ],
)
def test_get_reply_variants(message, expected_substring):
    """Basic sanity checks for the static chatbot responses."""
    reply = get_reply(message)
    assert isinstance(reply, str)
    assert expected_substring in reply
