from uuid import UUID

from arcana.types import ModelCapabilities, ModelConnection, ModelProvider, ModelTransport


def test_model_capabilities_defaults():
    caps = ModelCapabilities()
    assert caps.context_window == 4096
    assert caps.supports_tools is False
    assert caps.supports_vision is False
    assert caps.supports_streaming is True


def test_model_capabilities_custom():
    caps = ModelCapabilities(context_window=200000, supports_tools=True, supports_vision=True)
    assert caps.context_window == 200000
    assert caps.supports_tools is True
    assert caps.supports_vision is True


def test_model_connection_defaults():
    conn = _make_connection()
    assert conn.transport == ModelTransport.API
    assert conn.endpoint == ""
    assert conn.status == "unknown"
    assert conn.cost_per_1k_input_tokens is None
    assert conn.cost_per_1k_output_tokens is None
    assert isinstance(conn.capabilities, ModelCapabilities)
    assert isinstance(conn.id, UUID)


def test_model_connection_is_local_true():
    conn = _make_connection(provider=ModelProvider.OLLAMA)
    assert conn.is_local is True


def test_model_connection_is_local_false_anthropic():
    conn = _make_connection(provider=ModelProvider.ANTHROPIC)
    assert conn.is_local is False


def test_model_connection_is_local_false_openai():
    conn = _make_connection(provider=ModelProvider.OPENAI)
    assert conn.is_local is False


def test_model_connection_with_cost():
    conn = _make_connection(
        provider=ModelProvider.ANTHROPIC,
        cost_per_1k_input_tokens=0.003,
        cost_per_1k_output_tokens=0.015,
    )
    assert conn.cost_per_1k_input_tokens == 0.003
    assert conn.cost_per_1k_output_tokens == 0.015


def test_model_provider_values():
    assert ModelProvider.OLLAMA == "ollama"
    assert ModelProvider.ANTHROPIC == "anthropic"
    assert ModelProvider.OPENAI == "openai"
    assert ModelProvider.OPENAI_COMPAT == "openai_compat"
    assert ModelProvider.CUSTOM == "custom"


def test_model_transport_values():
    assert ModelTransport.SDK == "sdk"
    assert ModelTransport.API == "api"
    assert ModelTransport.LOCAL_SOCKET == "local_socket"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connection(**kwargs) -> ModelConnection:
    defaults = dict(
        name="test-model",
        provider=ModelProvider.OLLAMA,
        default_model="hermes-3",
    )
    return ModelConnection(**{**defaults, **kwargs})
