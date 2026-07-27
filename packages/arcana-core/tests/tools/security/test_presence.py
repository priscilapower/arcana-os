"""The presence gate — every guard in source has an adversarial test.

Coverage percentage can sit at 95% while one specific traversal or SSRF case is
untested; a presence check is the stronger guarantee the security tier turns on.
:func:`~tests.tools.security.catalog.required_guards` reads the guards that exist
in source (the enforced guardrail rule types, the registered security-bearing
builtins, and the fixed cross-cutting Finding classes); each catalog module
declares the guards it discharges in a module-level ``COVERS``. This test unions
those declarations and asserts they span every required guard — so a guard added
to source without a test, or a catalog module deleted, fails CI here.

It also refuses an *empty* claim: a module that declares ``COVERS`` must carry at
least one real ``test_`` function, so coverage can't be asserted into existence.
"""

import importlib
import pkgutil
from types import ModuleType

import pytest

from tests.tools import security
from tests.tools.security.catalog import required_guards

pytestmark = pytest.mark.security


def _catalog_modules() -> dict[str, ModuleType]:
    """Every ``test_*`` module in the security package that declares ``COVERS``."""
    modules: dict[str, ModuleType] = {}
    for info in pkgutil.iter_modules(security.__path__):
        if not info.name.startswith("test_"):
            continue
        module = importlib.import_module(f"{security.__name__}.{info.name}")
        if hasattr(module, "COVERS"):
            modules[info.name] = module
    return modules


def test_every_source_guard_has_a_security_test():
    modules = _catalog_modules()
    covered: set[str] = set()
    for module in modules.values():
        covered |= set(module.COVERS)

    missing = required_guards() - covered
    assert not missing, (
        f"guards with no security test: {sorted(missing)}. "
        f"Add an adversarial test and declare it in the covering module's COVERS."
    )


def test_no_module_claims_coverage_without_a_real_test():
    for name, module in _catalog_modules().items():
        has_test = any(attr.startswith("test_") and callable(getattr(module, attr)) for attr in dir(module))
        assert has_test, f"{name} declares COVERS but has no test_ function"


def test_the_catalog_declares_the_expected_finding_classes():
    """A human-readable anchor: the Finding taxonomy the tier is built around."""
    required = required_guards()
    # The marquee classes the spec enumerates must each be represented.
    for guard in ("builtin:pathjail", "builtin:egress", "mcp:changed_withheld", "secret:no_plaintext"):
        assert guard in required
