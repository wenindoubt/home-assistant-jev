"""The deployed client is bundled. All transport tests use fake sessions."""

import json
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from custom_components.jev.client import (
    Choice,
    ChoiceAnswer,
    JevAuthError,
    JevClient,
    JevConnectionError,
    JevOverloadedError,
    JevRateLimitError,
    JevResponseError,
    JevValidationError,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)
from custom_components.jev.client.models import parse_answer

REPLY = {
    "model": "jev-1.13.0",
    "answers": {
        "flag": {"type": "noul", "noul": 0.9},
        "action": {
            "type": "choice",
            "choice": "off",
            "probabilities": {"off": 0.9, "on": 0.1},
            "confidence": 0.8,
        },
        "level": {
            "type": "score",
            "score": 0.2,
            "legend": {"0": "low", "1": "high"},
            "probabilities": {"0": 0.8, "1": 0.2},
            "confidence": 0.6,
        },
    },
    "usage": {"input_tokens": 321, "output_tokens": 42},
}
QUESTIONS = {
    "flag": Noul("Is this true?"),
    "action": Choice("Which action?", {"off": None, "on": None}),
    "level": Score("Which level?", ["low", "high"]),
}


class FakeResponse:
    def __init__(self, status=200, data=None, headers=None, error=None):
        self.status = status
        self.data = REPLY if data is None else data
        self.headers = headers or {}
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *exc):
        return None

    async def text(self):
        return json.dumps(self.data)

    async def json(self, **kwargs):
        return self.data


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []
        self.close = AsyncMock()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


async def test_bundled_client_wire_format_and_typed_answers():
    session = FakeSession()
    client = JevClient("fake-key", session=session, base_url="https://192.0.2.10/proxy/")
    response = await client.ask({"command": "off"}, QUESTIONS)
    url, kwargs = session.calls[0]
    assert url == "https://192.0.2.10/proxy/v1/systemone"
    assert kwargs["headers"] == {"Authorization": "Bearer fake-key"}
    assert kwargs["json"]["questions"] == {
        key: question.as_payload() for key, question in QUESTIONS.items()
    }
    assert response.model == "jev-1.13.0"
    assert response.usage.input_tokens == 321
    assert response.usage.output_tokens == 42
    assert response.latency_ms >= 0
    assert response["flag"].value == 0.9
    assert response["action"].value == "off"
    assert response["level"].value == 0.2
    assert response["level"].nearest_level == "low"
    assert response["level"].normalized == 0.2
    await client.async_close()
    session.close.assert_not_awaited()


async def test_keyless_endpoint_and_per_request_model():
    session = FakeSession()
    client = JevClient("", session=session)
    await client.ask("test", QUESTIONS, model="jev-pinned")
    _, kwargs = session.calls[0]
    assert kwargs["headers"] == {}
    assert kwargs["json"]["model"] == "jev-pinned"


async def test_owned_session_is_closed_and_can_be_recreated():
    first, second = FakeSession(), FakeSession()
    with patch(
        "custom_components.jev.client.client.aiohttp.ClientSession",
        side_effect=[first, second],
    ) as constructor:
        client = JevClient("fake-key")
        async with client as entered:
            assert entered is client
            await client.ask("test", QUESTIONS)
        first.close.assert_awaited_once()
        await client.async_close()
        await client.ask("test", QUESTIONS)
        await client.async_close()
        second.close.assert_awaited_once()
        assert constructor.call_count == 2


async def test_empty_questions_never_send_a_request():
    session = FakeSession()
    with pytest.raises(ValueError, match="at least one"):
        await JevClient("fake-key", session=session).ask("test", {})
    assert session.calls == []


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, JevAuthError),
        (422, JevValidationError),
        (429, JevRateLimitError),
        (529, JevOverloadedError),
        (500, JevResponseError),
    ],
)
async def test_http_errors_map_to_owned_exception_types_without_retry(status, error):
    session = FakeSession(FakeResponse(status=status, headers={"Retry-After": "2"}))
    with pytest.raises(error) as caught:
        await JevClient("fake-key", session=session).ask("test", QUESTIONS)
    if status == 429:
        assert caught.value.retry_after == 2
    assert len(session.calls) == 1


async def test_rate_limit_without_retry_after():
    session = FakeSession(FakeResponse(status=429))
    with pytest.raises(JevRateLimitError) as caught:
        await JevClient("fake-key", session=session).ask("test", QUESTIONS)
    assert caught.value.retry_after is None


@pytest.mark.parametrize("error", [aiohttp.ClientError("offline"), TimeoutError()])
async def test_transport_failures_are_client_errors(error):
    session = FakeSession(FakeResponse(error=error))
    with pytest.raises(JevConnectionError):
        await JevClient("fake-key", session=session).ask("test", QUESTIONS)
    assert len(session.calls) == 1


@pytest.mark.parametrize("data", [[], {"answers": []}, {}])
def test_malformed_response_is_not_a_valid_result(data):
    with pytest.raises(JevResponseError):
        JevClient._parse(data, 0)


def test_optional_response_fields_have_safe_empty_defaults():
    response = JevClient._parse({"answers": {}}, 0)
    assert response.model == ""
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "unknown"},
        {"type": "noul"},
        {"type": "noul", "noul": "not a number"},
        {"type": "choice", "choice": "off"},
        {"type": "score", "score": None},
    ],
)
def test_unreadable_answers_are_not_silently_used(raw):
    with pytest.raises(JevResponseError):
        parse_answer("test", raw)


@pytest.mark.parametrize("size", [0, 1, 256])
def test_choice_bounds(size):
    with pytest.raises(ValueError):
        Choice("Pick one", {str(i): None for i in range(size)})


@pytest.mark.parametrize("size", [0, 1, 11])
def test_score_bounds(size):
    with pytest.raises(ValueError):
        Score("Rate this", ["level"] * size)


def test_question_payloads_and_answer_properties():
    assert Noul("Question", true="Yes", false="No").as_payload()["criteria"] == {
        "true": "Yes",
        "false": "No",
    }
    assert Noul("Question").as_payload() == {
        "type": "noul",
        "instructions": "Question",
    }
    assert NoulAnswer(0.5).value == 0.5
    assert ChoiceAnswer("on", {"on": 1.0}, 1.0).value == "on"
    assert ScoreAnswer(0, {}, {"0": 1}, 1).normalized == 0
    assert ScoreAnswer(0, {}, {"0": 1}, 1).nearest_level == ""
