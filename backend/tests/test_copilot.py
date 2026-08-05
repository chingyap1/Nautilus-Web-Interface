"""Phase O1a Strategy Copilot persistence and authorization tests."""

import asyncio

import aiosqlite
import copilot_store
import database
from auth_jwt import create_access_token


def _create_workspace(client, **overrides):
    payload = {"title": "BTC momentum research", **overrides}
    return client.post("/api/copilot/workspaces", json=payload)


def test_workspace_round_trip_uses_authenticated_owner(client):
    response = _create_workspace(client)
    assert response.status_code == 201
    workspace = response.json()["workspace"]
    assert workspace["owner_id"] == "admin"
    assert workspace["lifecycle"] == "IDEA"
    assert workspace["strategy_id"] is None

    listed = client.get("/api/copilot/workspaces")
    assert listed.status_code == 200
    assert listed.json()["workspaces"] == [workspace]

    detail = client.get(f"/api/copilot/workspaces/{workspace['id']}")
    assert detail.json()["workspace"] == workspace


def test_conversation_and_messages_are_durable(client):
    workspace = _create_workspace(client).json()["workspace"]
    conversation_response = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations",
        json={"title": "Entry signal"},
    )
    assert conversation_response.status_code == 201
    conversation = conversation_response.json()["conversation"]

    sent = client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "Use a volatility filter."},
    )
    assert sent.status_code == 201
    body = sent.json()
    assert body["message"]["role"] == "user"
    assert body["acknowledgement"]["role"] == "system"
    assert body["acknowledgement"]["status"] == "queued_for_supervisor"
    assert "not enabled" in body["acknowledgement"]["content"]

    messages = client.get(f"/api/copilot/conversations/{conversation['id']}/messages").json()[
        "messages"
    ]
    assert messages == [body["message"], body["acknowledgement"]]

    second = client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "Also define an exit rule."},
    ).json()
    messages = client.get(f"/api/copilot/conversations/{conversation['id']}/messages").json()[
        "messages"
    ]
    assert [message["sequence"] for message in messages] == [0, 1, 2, 3]
    assert messages[-2:] == [second["message"], second["acknowledgement"]]


def test_workspace_supports_multiple_durable_conversations(client):
    workspace = _create_workspace(client).json()["workspace"]
    first = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations",
        json={"title": "Entry thesis"},
    ).json()["conversation"]
    second = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations",
        json={"title": "Exit thesis"},
    ).json()["conversation"]

    response = client.get(f"/api/copilot/workspaces/{workspace['id']}/conversations")
    assert response.status_code == 200
    assert {conversation["id"] for conversation in response.json()["conversations"]} == {
        first["id"],
        second["id"],
    }


def test_cross_owner_resources_are_not_disclosed(client):
    workspace = _create_workspace(client).json()["workspace"]
    conversation = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations",
        json={"title": "Private"},
    ).json()["conversation"]
    other_token = create_access_token({"sub": "other-user", "role": "trader"})
    headers = {"Authorization": f"Bearer {other_token}"}

    assert (
        client.get(f"/api/copilot/workspaces/{workspace['id']}", headers=headers).status_code == 404
    )
    assert (
        client.get(
            f"/api/copilot/conversations/{conversation['id']}/messages", headers=headers
        ).status_code
        == 404
    )
    assert client.get("/api/copilot/workspaces", headers=headers).json()["workspaces"] == []


def test_token_without_subject_cannot_access_copilot(client):
    token = create_access_token({"role": "trader"})
    response = client.get("/api/copilot/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_validation_and_missing_resources(client):
    assert _create_workspace(client, title="   ").status_code == 422
    assert _create_workspace(client, title="x" * 121).status_code == 422
    assert _create_workspace(client, strategy_id="missing").status_code == 422
    assert client.get("/api/copilot/workspaces/missing").status_code == 404

    workspace = _create_workspace(client).json()["workspace"]
    conversation = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations", json={}
    ).json()["conversation"]
    assert (
        client.post(
            f"/api/copilot/conversations/{conversation['id']}/messages",
            json={"content": "   "},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/copilot/conversations/{conversation['id']}/messages",
            json={"content": "x" * 10_001},
        ).status_code
        == 422
    )


def test_copilot_actions_are_audited_without_commands(client):
    workspace = _create_workspace(client).json()["workspace"]
    conversation = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/conversations", json={}
    ).json()["conversation"]
    client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "Draft an idea only."},
    )

    audit = asyncio.run(database.get_audit_logs(action="copilot_message_created"))
    assert audit[0]["user_id"] == "admin"

    async def command_count() -> int:
        async with aiosqlite.connect(database.DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM commands") as cursor:
                return (await cursor.fetchone())[0]

    assert asyncio.run(command_count()) == 0


def test_artifact_approval_binds_lifecycle_to_current_revision(client):
    workspace = _create_workspace(client).json()["workspace"]
    blocked = client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    assert blocked.status_code == 409

    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={
            "kind": "specification",
            "title": "Momentum specification",
            "content": "Define entry, exit, and risk.",
        },
    )
    assert created.status_code == 201
    artifact, first = created.json()["artifact"], created.json()["revision"]
    assert first["content_hash"]

    approved = client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{first['id']}/approval",
        json={"decision": "approved", "reason": "Ready for a draft."},
    )
    assert approved.status_code == 201
    assert (
        client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance").json()[
            "workspace"
        ]["lifecycle"]
        == "SPECIFICATION"
    )

    draft = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={"kind": "strategy_draft", "title": "Momentum draft", "content": "Pseudo-code v1."},
    ).json()
    revised = client.post(
        f"/api/copilot/artifacts/{draft['artifact']['id']}/revisions",
        json={"content": "Pseudo-code v2."},
    ).json()["revision"]
    client.post(
        f"/api/copilot/artifacts/{draft['artifact']['id']}/revisions/{draft['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Old revision only."},
    )
    assert (
        client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance").status_code
        == 409
    )
    client.post(
        f"/api/copilot/artifacts/{draft['artifact']['id']}/revisions/{revised['id']}/approval",
        json={"decision": "approved", "reason": "Current revision is ready."},
    )
    advanced = client.post(f"/api/copilot/workspaces/{workspace['id']}/lifecycle/advance")
    assert advanced.json()["workspace"]["lifecycle"] == "DRAFT"
    history = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()[
        "transitions"
    ]
    assert [entry["to_lifecycle"] for entry in history] == ["DRAFT", "SPECIFICATION"]


def test_artifacts_are_owner_scoped_and_validate_decisions(client):
    workspace = _create_workspace(client).json()["workspace"]
    artifact = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={"kind": "specification", "title": "Private", "content": "Private content."},
    ).json()["artifact"]
    other_token = create_access_token({"sub": "other-user", "role": "trader"})
    headers = {"Authorization": f"Bearer {other_token}"}
    assert (
        client.get(
            f"/api/copilot/artifacts/{artifact['id']}/revisions", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/copilot/workspaces/{workspace['id']}/artifacts",
            json={"kind": "invalid", "title": "Bad", "content": "x"},
        ).status_code
        == 422
    )


def test_latest_current_revision_decision_controls_eligibility_and_is_auditable(client):
    workspace = _create_workspace(client).json()["workspace"]
    artifact, revision = (
        client.post(
            f"/api/copilot/workspaces/{workspace['id']}/artifacts",
            json={"kind": "specification", "title": "Spec", "content": "Review me."},
        )
        .json()
        .values()
    )

    approve = client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Complete."},
    )
    assert approve.status_code == 201
    assert client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()["eligibility"][
        "eligible"
    ]

    reject = client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "rejected", "reason": "Add exit constraints."},
    )
    assert reject.status_code == 201
    lifecycle = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()
    assert not lifecycle["eligibility"]["eligible"]
    approvals = client.get(f"/api/copilot/artifacts/{artifact['id']}/approvals").json()["approvals"]
    assert [approval["decision"] for approval in approvals] == ["rejected", "approved"]

    client.post(
        f"/api/copilot/artifacts/{artifact['id']}/revisions/{revision['id']}/approval",
        json={"decision": "approved", "reason": "Exit constraints added."},
    )
    assert client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()["eligibility"][
        "eligible"
    ]


def test_advance_lifecycle_is_atomic_for_same_workspace(client):
    workspace = _create_workspace(client).json()["workspace"]
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/artifacts",
        json={"kind": "specification", "title": "Spec", "content": "Ready."},
    ).json()
    client.post(
        f"/api/copilot/artifacts/{created['artifact']['id']}/revisions/{created['revision']['id']}/approval",
        json={"decision": "approved", "reason": "Ready to advance."},
    )

    async def advance_twice():
        return await asyncio.gather(
            copilot_store.advance_lifecycle(workspace, "admin"),
            copilot_store.advance_lifecycle(workspace, "admin"),
        )

    results = asyncio.run(advance_twice())
    assert sum(result is not None for result in results) == 1
    transitions = client.get(f"/api/copilot/workspaces/{workspace['id']}/lifecycle").json()[
        "transitions"
    ]
    assert len(transitions) == 1


def test_task_progress_is_durable_and_streamed_to_owner(client, monkeypatch):
    workspace = _create_workspace(client).json()["workspace"]
    delivered = []

    async def capture(owner_id, message):
        delivered.append((owner_id, message))

    monkeypatch.setattr("routers.copilot.manager.send_to", capture)
    created = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/tasks",
        json={"title": "Define validation plan"},
    )
    assert created.status_code == 201
    task = created.json()["task"]
    assert task["status"] == "pending"
    assert task["progress"] == 0

    updated = client.post(
        f"/api/copilot/tasks/{task['id']}/events",
        json={"status": "running", "progress": 40, "message": "Checking constraints"},
    )
    assert updated.status_code == 201
    body = updated.json()
    assert body["task"]["progress"] == 40
    assert body["event"]["sequence"] == 1
    assert delivered == [
        (
            "admin",
            {
                "type": "copilot_task_progress",
                "workspace_id": workspace["id"],
                "task": body["task"],
                "event": body["event"],
            },
        )
    ]

    tasks = client.get(f"/api/copilot/workspaces/{workspace['id']}/tasks").json()["tasks"]
    events = client.get(f"/api/copilot/tasks/{task['id']}/events").json()["events"]
    assert tasks == [body["task"]]
    assert [event["sequence"] for event in events] == [0, 1]
    assert events[0]["message"] == "Task created"


def test_task_progress_rejects_invalid_transitions_and_cross_owner_access(client):
    workspace = _create_workspace(client).json()["workspace"]
    task = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/tasks",
        json={"title": "Prepare evidence"},
    ).json()["task"]

    assert (
        client.post(
            f"/api/copilot/tasks/{task['id']}/events",
            json={"status": "running", "progress": 101, "message": "Invalid"},
        ).status_code
        == 422
    )
    client.post(
        f"/api/copilot/tasks/{task['id']}/events",
        json={"status": "running", "progress": 60, "message": "Running checks"},
    )
    assert (
        client.post(
            f"/api/copilot/tasks/{task['id']}/events",
            json={"status": "running", "progress": 50, "message": "Went backwards"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/copilot/tasks/{task['id']}/events",
            json={"status": "succeeded", "progress": 90, "message": "Incomplete"},
        ).status_code
        == 409
    )
    completed = client.post(
        f"/api/copilot/tasks/{task['id']}/events",
        json={"status": "succeeded", "progress": 100, "message": "Evidence ready"},
    )
    assert completed.status_code == 201
    assert (
        client.post(
            f"/api/copilot/tasks/{task['id']}/events",
            json={"status": "running", "progress": 100, "message": "Restart"},
        ).status_code
        == 409
    )

    other_token = create_access_token({"sub": "other-user", "role": "trader"})
    headers = {"Authorization": f"Bearer {other_token}"}
    assert (
        client.get(f"/api/copilot/workspaces/{workspace['id']}/tasks", headers=headers).status_code
        == 404
    )
    assert client.get(f"/api/copilot/tasks/{task['id']}/events", headers=headers).status_code == 404


def test_task_progress_events_are_serialized(client):
    workspace = _create_workspace(client).json()["workspace"]
    task = client.post(
        f"/api/copilot/workspaces/{workspace['id']}/tasks",
        json={"title": "Run independent checks"},
    ).json()["task"]

    async def progress_twice():
        return await asyncio.gather(
            copilot_store.append_task_event(task["id"], "running", 50, "Check A", "admin"),
            copilot_store.append_task_event(task["id"], "running", 50, "Check B", "admin"),
        )

    results = asyncio.run(progress_twice())
    assert sorted(result[1]["sequence"] for result in results) == [1, 2]
    events = client.get(f"/api/copilot/tasks/{task['id']}/events").json()["events"]
    assert [event["sequence"] for event in events] == [0, 1, 2]


def test_task_progress_websocket_delivery_is_owner_scoped():
    from state import ConnectionManager

    class Socket:
        def __init__(self):
            self.messages = []

        async def accept(self):
            return None

        async def send_json(self, message):
            self.messages.append(message)

    first, second = Socket(), Socket()
    manager = ConnectionManager()

    async def deliver():
        await manager.connect(first, "first-owner")
        await manager.connect(second, "second-owner")
        await manager.send_to("first-owner", {"type": "copilot_task_progress"})

    asyncio.run(deliver())
    assert first.messages == [{"type": "copilot_task_progress"}]
    assert second.messages == []
