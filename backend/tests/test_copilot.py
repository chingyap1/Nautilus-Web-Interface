"""Phase O1a Strategy Copilot persistence and authorization tests."""

import asyncio
import aiosqlite

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

    messages = client.get(
        f"/api/copilot/conversations/{conversation['id']}/messages"
    ).json()["messages"]
    assert messages == [body["message"], body["acknowledgement"]]

    second = client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "Also define an exit rule."},
    ).json()
    messages = client.get(
        f"/api/copilot/conversations/{conversation['id']}/messages"
    ).json()["messages"]
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

    assert client.get(
        f"/api/copilot/workspaces/{workspace['id']}", headers=headers
    ).status_code == 404
    assert client.get(
        f"/api/copilot/conversations/{conversation['id']}/messages", headers=headers
    ).status_code == 404
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
    assert client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "   "},
    ).status_code == 422
    assert client.post(
        f"/api/copilot/conversations/{conversation['id']}/messages",
        json={"content": "x" * 10_001},
    ).status_code == 422


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