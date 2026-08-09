from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


def _write_schema(path: Path, *, fetched_at: float | None = None) -> Path:
    schema = {
        "schema_version": "simulation-settings-v1",
        "fetched_at": fetched_at if fetched_at is not None else time.time(),
        "context": {"region": "USA", "universe": "TOP3000", "delay": 1},
        "defaults": {
            "alpha_type": "REGULAR",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 4,
            "neutralization": "MARKET",
            "truncation": 0.08,
            "language": "FASTEXPR",
        },
        "allowed_values": {
            "alpha_type": ["REGULAR"],
            "region": ["USA"],
            "universe": ["TOP3000"],
            "delay": [1],
            "decay": [0, 4, 8],
            "neutralization": ["MARKET", "INDUSTRY", "SUBINDUSTRY"],
            "truncation": [0.08, 0.1],
            "language": ["FASTEXPR"],
        },
    }
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def test_contract_canonicalizes_only_unique_case_insensitive_match(tmp_path: Path) -> None:
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    contract = SimulationSettingsContract.load(_write_schema(tmp_path / "settings.json"))

    settings = contract.prepare({"neutralization": "industry"})

    assert settings["neutralization"] == "INDUSTRY"
    with pytest.raises(ValueError, match="neutralization"):
        contract.prepare({"neutralization": "not-a-platform-value"})


def test_contract_rejects_stale_schema(tmp_path: Path) -> None:
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    path = _write_schema(tmp_path / "settings.json", fetched_at=time.time() - 48 * 3600)

    with pytest.raises(ValueError, match="stale"):
        SimulationSettingsContract.load(path, max_age_hours=24)


def test_request_store_refuses_invalid_settings_before_persisting(tmp_path: Path) -> None:
    from alpha_mining.factory.simulation_requests import SimulationRequestStore
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract
    from alpha_mining.storage.migrations import migrate
    from alpha_mining.storage.sqlite_store import SqliteRunLog

    database = tmp_path / "requests.sqlite"
    SqliteRunLog(database).initialize_schema()
    migrate(database)
    contract = SimulationSettingsContract.load(_write_schema(tmp_path / "settings.json"))
    store = SimulationRequestStore(database, settings_contract=contract)

    claim = store.claim(
        "rank(ts_delta(close,21))",
        {"neutralization": "invalid"},
    )

    assert claim.claimed is False
    assert claim.reason == "invalid_simulation_settings"


def test_gateway_refuses_invalid_settings_before_simulation_post(tmp_path: Path) -> None:
    from alpha_mining.platform.gateway import PlatformGateway

    class Client:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def authenticate(self) -> None:
            return None

        def request(self, method: str, *_args, **_kwargs):
            self.requests.append(method)
            raise AssertionError("invalid settings must not reach the platform client")

    gateway = PlatformGateway(
        database=tmp_path / "gateway.sqlite",
        settings_schema_path=_write_schema(tmp_path / "settings.json"),
    )
    client = Client()
    gateway.client = client

    with pytest.raises(ValueError, match="neutralization"):
        gateway.simulate(expression="rank(close)", settings={"neutralization": "invalid"})

    assert client.requests == []


def test_prepared_settings_carry_no_alpha_type(tmp_path: Path) -> None:
    """alpha_type is a payload field, not a settings field.

    The live endpoint refuses it inside ``settings``:
        POST /simulations -> HTTP 400
        {"settings":{"alphaType":["Unexpected property."], ...}}
    It still has to be validated against the synchronized schema, because the
    gateway cross-checks the outer ``type`` against it -- so it is checked and
    then dropped, not left unchecked.
    """
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    contract = SimulationSettingsContract.load(_write_schema(tmp_path / "settings.json"))

    prepared = contract.prepare({"neutralization": "MARKET"})

    assert "alpha_type" not in prepared
    assert contract.alpha_type({"neutralization": "MARKET"}) == "REGULAR"
    # An out-of-schema alpha_type must still be refused rather than ignored.
    with pytest.raises(ValueError, match="alpha_type"):
        contract.alpha_type({"alpha_type": "SUPER"})


def test_prepared_settings_keep_platform_required_extras(tmp_path: Path) -> None:
    """Keys the platform requires but the schema does not enumerate must survive.

    The synced schema carries no ``instrumentType`` at all, yet the endpoint
    refuses a payload without it ("This field is required."), so prepare() must
    pass a caller-supplied value through untouched.
    """
    from alpha_mining.platform.simulation_contract import SimulationSettingsContract

    contract = SimulationSettingsContract.load(_write_schema(tmp_path / "settings.json"))

    prepared = contract.prepare({"instrumentType": "EQUITY", "neutralization": "MARKET"})

    assert prepared["instrumentType"] == "EQUITY"


def test_gateway_sends_settings_without_alpha_type_and_type_at_top_level(tmp_path: Path) -> None:
    """The wire payload must match what the platform accepts."""
    from alpha_mining.platform.gateway import PlatformGateway

    captured: dict[str, object] = {}

    class Client:
        def authenticate(self) -> None:
            return None

        def request(self, method: str, url: str, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            raise RuntimeError("stop after capturing the payload")

    gateway = PlatformGateway(
        database=tmp_path / "gateway.sqlite",
        settings_schema_path=_write_schema(tmp_path / "settings.json"),
    )
    gateway.client = Client()

    with pytest.raises(RuntimeError, match="stop after capturing"):
        gateway.simulate(
            expression="rank(close)",
            settings={"instrumentType": "EQUITY", "neutralization": "MARKET"},
        )

    payload = captured["json"]
    assert payload["type"] == "REGULAR"
    assert payload["regular"] == "rank(close)"
    assert "alpha_type" not in payload["settings"]
    assert "alphaType" not in payload["settings"]
    assert payload["settings"]["instrumentType"] == "EQUITY"


def test_gateway_refuses_outer_alpha_type_that_disagrees_with_settings(tmp_path: Path) -> None:
    from alpha_mining.platform.gateway import PlatformGateway

    gateway = PlatformGateway(
        database=tmp_path / "gateway.sqlite",
        settings_schema_path=_write_schema(tmp_path / "settings.json"),
    )
    gateway.client = type("Client", (), {"authenticate": lambda _self: None})()

    with pytest.raises(ValueError, match="alpha_type"):
        gateway.simulate(
            expression="rank(close)", settings={"alpha_type": "REGULAR"}, alpha_type="SUPER",
        )
