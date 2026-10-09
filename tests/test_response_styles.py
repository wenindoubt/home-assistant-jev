"""Speech profiles cannot alter routing, hide failures, or spend extra tokens."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent as ha_intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.jev.client import ChoiceAnswer
from custom_components.jev.conversation import JevConversationEntity
from custom_components.jev.select import JevResponseStyleSelect

from .conftest import build_response
from .test_conversation import answer_set, converse, unsure_between
from .test_conversation import house as house

STYLE = "select.jev_response_style"
STYLES = ("minimal", "jarvis", "pirate")


def make_agent(hass, entry):
    agent = JevConversationEntity(entry)
    agent.hass = hass
    return agent


async def choose(hass, style):
    await hass.services.async_call(
        "select", "select_option", {"entity_id": STYLE, "option": style}, blocking=True
    )


async def test_the_default_is_minimal_and_only_three_styles_exist(hass, loaded_entry):
    state = hass.states.get(STYLE)
    assert state.state == "minimal"
    assert state.attributes["options"] == list(STYLES)
    registered = er.async_get(hass).async_get(STYLE)
    assert registered.entity_category.value == "config"
    assert registered.unique_id == f"{loaded_entry.entry_id}_response_style"


@pytest.mark.parametrize("style", STYLES)
async def test_style_changes_are_live_local_and_do_not_reload(
    hass, loaded_entry, mock_client, style
):
    runtime = loaded_entry.runtime_data
    options = dict(loaded_entry.options)
    calls = mock_client.ask.await_count
    with patch.object(hass.config_entries, "async_reload") as reload_entry:
        await choose(hass, style)
        await hass.async_block_till_done()
    assert hass.states.get(STYLE).state == style
    assert loaded_entry.runtime_data is runtime
    assert runtime.response_style == style
    assert dict(loaded_entry.options) == options
    assert mock_client.ask.await_count == calls
    reload_entry.assert_not_called()


@pytest.mark.parametrize(
    "saved,expected", [("pirate", "pirate"), ("standard", "minimal")]
)
async def test_a_saved_style_is_restored_or_safely_reset(
    hass, config_entry, mock_client, saved, expected
):
    mock_restore_cache(hass, [State(STYLE, saved)])
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(STYLE).state == expected
    assert config_entry.runtime_data.response_style == expected


async def test_the_style_survives_an_integration_reload(hass, loaded_entry):
    await choose(hass, "pirate")
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(STYLE).state == "pirate"
    assert loaded_entry.runtime_data.response_style == "pirate"


async def test_invalid_styles_are_rejected_without_changing_the_value(hass, loaded_entry):
    entity = JevResponseStyleSelect(loaded_entry)
    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("standard")
    assert loaded_entry.runtime_data.response_style == "minimal"
    assert hass.states.get(STYLE).state == "minimal"


@pytest.mark.parametrize(
    "style,expected",
    [("minimal", "Done."), ("jarvis", "Done, as requested."), ("pirate", "Aye, done.")],
)
async def test_actions_change_wording_not_targets_or_api_calls(
    hass, house, mock_client, style, expected
):
    await choose(hass, style)
    mock_client.ask.return_value = build_response(**answer_set())
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))
    before = mock_client.ask.await_count
    result = await converse(hass, "turn on the kitchen light")
    assert mock_client.ask.await_count == before + 1
    assert [call.data["entity_id"] for call in calls] == [["light.kitchen"]]
    assert result.response.speech["plain"]["speech"] == expected
    assert result.response.success_results
    assert not result.response.failed_results


@pytest.mark.parametrize(
    "style,expected",
    [
        ("minimal", "Kitchen light: off."),
        ("jarvis", "Kitchen light is off"),
        ("pirate", "Kitchen light be off."),
    ],
)
async def test_state_questions_use_the_current_profile(
    hass, house, mock_client, style, expected
):
    await choose(hass, style)
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.98)
        )
    )
    result = await converse(hass, "is the kitchen light on")
    assert result.response.speech["plain"]["speech"] == expected
    assert hass.states.get("light.kitchen").state == "off"


@pytest.mark.parametrize(
    "style,expected",
    [
        ("minimal", "Which: Kitchen light or Office light?"),
        ("jarvis", "Do you mean Kitchen light or Office light?"),
        ("pirate", "Kitchen light or Office light, captain?"),
    ],
)
async def test_clarifications_keep_both_candidates_and_do_not_act(
    hass, house, mock_client, style, expected
):
    await choose(hass, style)
    mock_client.ask.return_value = build_response(
        **unsure_between("light.kitchen", "light.office")
    )
    result = await converse(hass, "turn on the light")
    assert result.continue_conversation
    assert result.response.speech["plain"]["speech"] == expected
    assert hass.states.get("light.kitchen").state == "off"
    assert hass.states.get("light.office").state == "off"


@pytest.mark.parametrize(
    "style,expected",
    [
        ("minimal", "Kitchen light: already on."),
        ("jarvis", "Kitchen light is already on."),
        ("pirate", "Kitchen light: already on, captain."),
    ],
)
async def test_already_satisfied_commands_remain_no_ops(
    hass, house, mock_client, style, expected
):
    await choose(hass, style)
    hass.states.async_set("light.kitchen", "on", {"friendly_name": "Kitchen light"})
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(
                choice="turn_on",
                probabilities={"turn_on": 0.44, "get_state": 0.31, "none_of_these": 0.25},
                confidence=0.28,
            )
        )
    )
    calls = []
    hass.services.async_register("light", "turn_on", lambda call: calls.append(call))
    result = await converse(hass, "turn on the kitchen light")
    assert not calls
    assert result.response.speech["plain"]["speech"] == expected


@pytest.mark.parametrize("language", ["en-US", "en-GB"])
async def test_english_variants_use_the_selected_profile(
    hass, house, mock_client, language
):
    await choose(hass, "pirate")
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.98)
        )
    )
    result = await converse(hass, "is the kitchen light on", language=language)
    assert result.response.speech["plain"]["speech"] == "Kitchen light be off."


@pytest.mark.parametrize("style", STYLES)
async def test_a_partial_failure_never_says_done(hass, house, mock_client, style):
    await choose(hass, style)
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="turn_off", probabilities={}, confidence=0.98),
            target_type=ChoiceAnswer(
                choice="everything", probabilities={}, confidence=0.98
            ),
            entity=ChoiceAnswer(
                choice="none_of_these", probabilities={}, confidence=0.99
            ),
        )
    )

    async def act(call):
        if call.data["entity_id"] == ["light.office"]:
            raise HomeAssistantError("Device unavailable")

    hass.services.async_register("light", "turn_off", act)
    result = await converse(hass, "turn off all the lights")
    assert result.response.failed_results
    speech = result.response.speech["plain"]["speech"]
    # The formatter must retain the intent layer's failure metadata. Parallel
    # service completion can reorder the names reported by Home Assistant itself.
    reported = ", ".join(target.name for target in result.response.failed_results)
    assert speech == f"Failed: {reported}."
    assert "done" not in speech.lower()


@pytest.mark.parametrize("style", STYLES)
async def test_errors_are_not_restyled_as_success(hass, loaded_entry, style):
    await choose(hass, style)
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    response.async_set_error(
        ha_intent.IntentResponseErrorCode.FAILED_TO_HANDLE, "Device unavailable"
    )
    before = response.as_dict()
    await agent._apply_reply_style(response, "HassTurnOn", "en")
    assert response.as_dict() == before


@pytest.mark.parametrize("failed", [False, True])
async def test_restyled_speech_cannot_leave_a_stale_ssml_success(
    hass, loaded_entry, failed
):
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    target = ha_intent.IntentResponseTarget(
        type=ha_intent.IntentResponseTargetType.ENTITY, name="Desk lamp", id="light.desk"
    )
    response.async_set_results([target], [target] if failed else [])
    response.async_set_speech("<speak>Done.</speak>", speech_type="ssml")
    await agent._apply_reply_style(response, "HassTurnOn", "en")
    assert set(response.speech) == {"plain"}
    assert response.speech["plain"]["speech"] == (
        "Failed: Desk lamp." if failed else "Done."
    )


async def test_an_action_with_no_success_evidence_keeps_its_reply(hass, loaded_entry):
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    response.async_set_speech("Not confirmed")
    await agent._apply_reply_style(response, "HassTurnOn", "en")
    assert response.speech["plain"]["speech"] == "Not confirmed"


@pytest.mark.parametrize("style", STYLES)
async def test_groups_summarize_states_without_changing_metadata(
    hass, loaded_entry, style
):
    await choose(hass, style)
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    response.response_type = ha_intent.IntentResponseType.QUERY_ANSWER
    response.async_set_states(
        [State("light.one", "on"), State("light.two", "off"), State("light.three", "on")]
    )
    response.async_set_speech("Three individual readings")
    before = response.as_dict()["data"]
    await agent._apply_reply_style(response, "HassGetState", "en")
    assert response.speech["plain"]["speech"] == "1 off, 2 on."
    assert response.as_dict()["data"] == before


@pytest.mark.parametrize(
    "states",
    [
        [State("light.one", "on"), State("light.two", "unavailable")],
        [State("light.one", "on"), State("switch.two", "on")],
        [
            State("sensor.one", "20", {"unit_of_measurement": "°C"}),
            State("sensor.two", "30"),
        ],
    ],
)
async def test_unavailable_mixed_and_numeric_groups_keep_named_readings(
    hass, loaded_entry, states
):
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    response.response_type = ha_intent.IntentResponseType.QUERY_ANSWER
    response.async_set_states(states)
    response.async_set_speech("Named readings including unavailable devices and units")
    await agent._apply_reply_style(response, "HassGetState", "en")
    assert (
        response.speech["plain"]["speech"]
        == "Named readings including unavailable devices and units"
    )


async def test_a_numeric_single_reading_keeps_its_units(hass, loaded_entry):
    agent = make_agent(hass, loaded_entry)
    response = ha_intent.IntentResponse("en")
    response.response_type = ha_intent.IntentResponseType.QUERY_ANSWER
    response.async_set_states(
        [
            State(
                "sensor.temperature",
                "21.5",
                {"friendly_name": "Temperature", "unit_of_measurement": "°C"},
            )
        ]
    )
    await agent._apply_reply_style(response, "HassGetState", "en")
    assert response.speech["plain"]["speech"] == "Temperature: 21.5 °C."


@pytest.mark.parametrize("style", STYLES)
async def test_other_languages_keep_localized_sentences(hass, house, mock_client, style):
    await choose(hass, style)
    mock_client.ask.return_value = build_response(
        **answer_set(
            action=ChoiceAnswer(choice="get_state", probabilities={}, confidence=0.98)
        )
    )
    result = await converse(hass, "is de kitchen light aan", language="nl")
    assert result.response.speech["plain"]["speech"] == "Kitchen light is uit"


@pytest.mark.parametrize("style", STYLES)
async def test_reviewed_mode_scripts_switch_the_live_selector_by_voice(
    hass, house, mock_client, style
):
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "examples/response_styles.yaml").read_text()
    )
    assert await async_setup_component(hass, "script", config)
    await hass.async_block_till_done()
    script = f"script.jev_{style}_mode"
    async_expose_entity(hass, "conversation", script, True)
    mock_client.ask.return_value = build_response(
        **answer_set(
            entity=ChoiceAnswer(choice=script, probabilities={}, confidence=0.99),
            domain=ChoiceAnswer(choice="script", probabilities={}, confidence=0.99),
        )
    )
    before = mock_client.ask.await_count
    await converse(hass, f"activate {style} mode")
    await hass.async_block_till_done()
    assert mock_client.ask.await_count == before + 1
    assert hass.states.get(STYLE).state == style
    assert house.runtime_data.response_style == style
