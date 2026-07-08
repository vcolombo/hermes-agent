"""HAConversationAdapter await-window bridge tests (stubbed agent)."""

import asyncio

import pytest
import pytest_asyncio
from wyoming.asr import Transcript
from wyoming.client import AsyncTcpClient
from wyoming.handle import Handled, NotHandled

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_mod = load_plugin_adapter("ha_conversation")


def make_config(**overrides):
    extra = {"bind_host": "127.0.0.1", "port": 0, "ack_after_seconds": 8.0,
             "announce_mode": "off", "announce_entity": "",
             "max_transcript_chars": 2000}
    extra.update(overrides)
    return PlatformConfig(enabled=True, extra=extra)


def test_validate_config():
    assert _mod.validate_config(make_config()) is True
    assert _mod.validate_config(make_config(port="not-a-port")) is False
    assert _mod.validate_config(make_config(announce_mode="bogus")) is False
    assert _mod.validate_config(
        make_config(announce_mode="default_device", announce_entity="")
    ) is False
    assert _mod.validate_config(
        make_config(announce_mode="default_device",
                    announce_entity="assist_satellite.kitchen")
    ) is True
    # empty extra: platform unconfigured
    assert _mod.validate_config(PlatformConfig(extra={})) is False


def test_apply_yaml_config_reads_platform_cfg_argument():
    section = {"enabled": True, "port": 10611, "announce_mode": "broadcast"}
    # nested-only style: top-level key absent, loader binds the block
    extra = _mod._apply_yaml_config({}, section)
    assert extra["port"] == 10611
    assert extra["announce_mode"] == "broadcast"
    assert _mod._apply_yaml_config({}, {}) is None


def test_register_declares_platform_entry():
    calls = {}

    class Ctx:
        def register_platform(self, **kwargs):
            calls.update(kwargs)

    _mod.register(Ctx())
    assert calls["name"] == "ha_conversation"
    assert callable(calls["check_fn"])
    assert calls["is_connected"] is _mod.validate_config  # config, not deps
    assert calls["apply_yaml_config_fn"] is _mod._apply_yaml_config
    hint = calls["platform_hint"].lower()
    assert "spoken" in hint or "aloud" in hint


@pytest_asyncio.fixture
async def rig(monkeypatch):
    """Connected adapter on an ephemeral port with a stubbed agent."""
    adapter = _mod.HAConversationAdapter(make_config())
    replies = {"handler": None}

    async def fake_handle_message(event):
        if replies["handler"] is not None:
            await replies["handler"](adapter, event)

    monkeypatch.setattr(adapter, "handle_message", fake_handle_message)
    assert await adapter.connect() is True
    try:
        yield adapter, replies
    finally:
        await adapter.disconnect()


async def _ask(adapter, text, context=None, timeout=5):
    async with AsyncTcpClient("127.0.0.1", adapter.server_port) as client:
        await client.write_event(Transcript(text, context=context or {}).event())
        return await asyncio.wait_for(client.read_event(), timeout)


@pytest.mark.asyncio
async def test_reply_reaches_asking_socket(rig):
    adapter, replies = rig

    async def reply(a, event):
        await a.send("home", f"you said {event.text}")

    replies["handler"] = reply
    event = await _ask(adapter, "hello there")
    assert Handled.is_type(event.type)
    assert Handled.from_event(event).text == "you said hello there"


@pytest.mark.asyncio
async def test_multi_send_joins_into_one_handled(rig):
    adapter, replies = rig

    async def chunked(a, event):
        await a.send("home", "part one.")
        await a.send("home", "part two.")

    replies["handler"] = chunked
    event = await _ask(adapter, "long answer please")
    assert Handled.from_event(event).text == "part one.\npart two."


@pytest.mark.asyncio
async def test_no_send_turn_gets_fallback_text(rig):
    adapter, replies = rig
    replies["handler"] = None  # agent produced nothing
    event = await _ask(adapter, "silent treatment")
    assert Handled.is_type(event.type)
    assert Handled.from_event(event).text  # some non-empty fallback


@pytest.mark.asyncio
async def test_empty_and_oversized_transcripts_not_handled(rig):
    adapter, replies = rig
    replies["handler"] = None
    event = await _ask(adapter, "")
    assert NotHandled.is_type(event.type)
    event = await _ask(adapter, "x" * 5000)
    assert NotHandled.is_type(event.type)


@pytest.mark.asyncio
async def test_send_outside_window_routes_to_announce(rig):
    adapter, replies = rig
    announced = []

    async def fake_announce(text, *, entity):
        announced.append((text, entity))
        from gateway.platforms.base import SendResult
        return SendResult(success=True)

    adapter._announce = fake_announce
    result = await adapter.send("home", "cron job finished")
    assert result.success is True
    assert announced == [("cron job finished", None)]


@pytest.mark.asyncio
async def test_agent_exception_speaks_error_not_nothandled(rig):
    adapter, replies = rig

    async def boom(a, event):
        raise RuntimeError("tool exploded")

    replies["handler"] = boom
    event = await _ask(adapter, "break please")
    assert Handled.is_type(event.type)
    assert "sorry" in (Handled.from_event(event).text or "").lower()


@pytest.mark.asyncio
async def test_concurrent_utterances_serialize_and_both_answer(rig):
    adapter, replies = rig

    async def reply(a, event):
        await asyncio.sleep(0.05)
        await a.send("home", f"answer: {event.text}")

    replies["handler"] = reply
    a, b = await asyncio.gather(_ask(adapter, "first"), _ask(adapter, "second"))
    texts = {Handled.from_event(a).text, Handled.from_event(b).text}
    assert texts == {"answer: first", "answer: second"}


@pytest.mark.asyncio
async def test_waiting_cap_returns_busy(rig):
    adapter, replies = rig

    async def slow(a, event):
        await asyncio.sleep(0.3)
        await a.send("home", f"answer: {event.text}")

    replies["handler"] = slow
    events = await asyncio.gather(
        *[_ask(adapter, f"q{i}", timeout=10) for i in range(9)]
    )
    texts = [Handled.from_event(e).text or "" for e in events]
    busy = [t for t in texts if "try again" in t.lower()]
    answered = [t for t in texts if t.startswith("answer:")]
    # exactly one over the cap of 8 is refused immediately; the rest queue
    assert len(busy) == 1
    assert len(answered) == 8
