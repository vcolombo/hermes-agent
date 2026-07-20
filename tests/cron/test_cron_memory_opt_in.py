"""Regression tests for opt-in memory access in LLM cron jobs.

Cron must remain memory-isolated by default, while explicit memory-maintenance
jobs can initialize the configured memory provider and its tools.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cron.scheduler import run_job
from tools.cronjob_tools import CRONJOB_SCHEMA, cronjob


@pytest.fixture
def isolated_cron_store(tmp_path, monkeypatch):
    cron_dir = tmp_path / "cron"
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir)
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", cron_dir / "output")
    return cron_dir


def test_cronjob_schema_exposes_allow_memory_as_opt_in_boolean():
    prop = CRONJOB_SCHEMA["parameters"]["properties"]["allow_memory"]

    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "opt-in" in prop["description"].lower()


def test_create_and_update_persist_allow_memory_without_changing_safe_default(
    isolated_cron_store,
):
    default_job = json.loads(
        cronjob(action="create", prompt="ordinary task", schedule="every 1h")
    )
    opted_in = json.loads(
        cronjob(
            action="create",
            prompt="maintain durable memory",
            schedule="every 1h",
            allow_memory=True,
        )
    )

    listing = json.loads(cronjob(action="list"))
    jobs = {job["job_id"]: job for job in listing["jobs"]}
    assert "allow_memory" not in jobs[default_job["job_id"]]
    assert jobs[opted_in["job_id"]]["allow_memory"] is True

    updated = json.loads(
        cronjob(
            action="update",
            job_id=opted_in["job_id"],
            allow_memory=False,
        )
    )
    assert "allow_memory" not in updated["job"]


def test_registered_tool_handler_forwards_allow_memory(isolated_cron_store):
    from tools.registry import registry

    entry = registry._tools["cronjob"]
    created = json.loads(
        entry.handler(
            {
                "action": "create",
                "prompt": "maintain memory",
                "schedule": "every 1h",
                "allow_memory": True,
            }
        )
    )

    listing = json.loads(cronjob(action="list"))
    stored = next(job for job in listing["jobs"] if job["job_id"] == created["job_id"])
    assert stored["allow_memory"] is True


@pytest.mark.parametrize(
    ("job_allow_memory", "expected_skip_memory"),
    [(None, True), (False, True), (True, False)],
)
def test_scheduler_only_initializes_memory_for_explicitly_opted_in_jobs(
    tmp_path,
    job_allow_memory,
    expected_skip_memory,
):
    job = {"id": "memory-job", "name": "memory", "prompt": "hello"}
    if job_allow_memory is not None:
        job["allow_memory"] = job_allow_memory
    fake_db = MagicMock()

    with (
        patch("cron.scheduler._hermes_home", tmp_path),
        patch("cron.scheduler._resolve_origin", return_value=None),
        patch("hermes_cli.env_loader.load_hermes_dotenv"),
        patch("hermes_cli.env_loader.reset_secret_source_cache"),
        patch("hermes_state.SessionDB", return_value=fake_db),
        patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "api_key": "test-key",
                "base_url": "https://example.invalid/v1",
                "provider": "openrouter",
                "api_mode": "chat_completions",
            },
        ),
        patch("run_agent.AIAgent") as mock_agent_cls,
    ):
        mock_agent = MagicMock()
        mock_agent.run_conversation.return_value = {"final_response": "ok"}
        mock_agent_cls.return_value = mock_agent

        success, _output, final_response, error = run_job(job)

    assert success is True
    assert final_response == "ok"
    assert error is None
    assert mock_agent_cls.call_args.kwargs["skip_memory"] is expected_skip_memory
