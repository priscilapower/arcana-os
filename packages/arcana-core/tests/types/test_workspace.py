from uuid import UUID, uuid4

from arcana.types import Workspace


def test_workspace_defaults():
    ws = Workspace(id="local", name="Local")
    assert ws.description == ""
    assert ws.owner_id is None
    assert ws.is_default is False
    assert ws.created_at is not None


def test_workspace_local_pattern():
    ws = Workspace(id="local", name="Local", is_default=True)
    assert ws.id == "local"
    assert ws.is_default is True


def test_workspace_with_owner():
    owner = uuid4()
    ws = Workspace(id="team-alpha", name="Team Alpha", owner_id=owner)
    assert ws.owner_id == owner
    assert isinstance(ws.owner_id, UUID)


def test_workspace_slug_id():
    ws = Workspace(id="my-team", name="My Team")
    assert ws.id == "my-team"


def test_workspace_description():
    ws = Workspace(id="local", name="Local", description="Default local workspace")
    assert ws.description == "Default local workspace"
