"""The layering rules, enforced.

A layered architecture that is only a directory convention decays the first
time someone reaches sideways for something convenient. These tests read the
import graph statically (no imports executed) and fail the build when a layer
reaches somewhere it must not.

The rules, and why each one is here rather than in a style guide:

- **domain imports no other layer.** This is the one that carries the value.
  It is why §6.2's verification rules can be exercised offline against a stub
  in milliseconds, and why replacing Qdrant or the LLM vendor cannot touch the
  logic that decides whether an answer is trustworthy.
- **core imports nothing internal.** It is the leaf every layer depends on;
  an edge out of it would make the whole graph cyclic.
- **services never import infrastructure at module scope.** They depend on
  ports. The `build_default_*` factories deliberately import concretions
  *inside* the function — that is the composition root, not a dependency.
- **infrastructure and domain never import the API.** Nothing below the
  presentation layer may know that HTTP exists.
- **no vendor SDK above infrastructure.** The check that would have caught
  the original `GenerationUnavailable` placement: an exception type defined
  next to a provider forces the route layer to import that provider to write
  an `except` clause.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Third-party packages that may only be imported from `app.infrastructure`.
VENDOR_SDKS = {
    "sqlalchemy",
    "qdrant_client",
    "elasticsearch",
    "sentence_transformers",
    "torch",
    "google",
    "openai",
    "pypdf",
    # WS-1 auth (frontend_plan.md §3): both live in app/infrastructure/auth/
    # for exactly this reason — a password hasher and a JWT codec are vendor
    # SDKs like any other, and `AuthService` (app/services/auth_service.py)
    # receives their functions through its constructor rather than importing
    # either at module scope.
    "bcrypt",
    "jwt",
}


def _modules():
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP.parent)
        yield ".".join(rel.with_suffix("").parts).removesuffix(".__init__"), path


def _imports(path: pathlib.Path, *, module_scope_only: bool = False):
    """Yield imported module names. `module_scope_only` skips function-local
    imports, which are how the composition roots reach concretions on purpose.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    if module_scope_only:
        # Walk only nodes reachable without entering a function body.
        nodes: list[ast.AST] = []
        stack = list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes.append(node)
            if isinstance(node, ast.ClassDef):
                stack.extend(node.body)
            elif isinstance(node, ast.If):
                stack.extend(node.body + node.orelse)
    else:
        nodes = list(ast.walk(tree))

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def _violations(layer: str, forbidden: tuple[str, ...], *, module_scope_only: bool = False):
    out = []
    for name, path in _modules():
        if not name.startswith(f"app.{layer}"):
            continue
        for imported in _imports(path, module_scope_only=module_scope_only):
            if any(imported == f or imported.startswith(f + ".") for f in forbidden):
                out.append(f"{name} imports {imported}")
    return out


def test_domain_depends_on_no_layer_but_core():
    assert _violations(
        "domain", ("app.infrastructure", "app.services", "app.api", "app.cli")
    ) == []


def test_core_is_a_leaf():
    assert _violations(
        "core", ("app.domain", "app.infrastructure", "app.services", "app.api", "app.cli")
    ) == []


def test_services_reach_infrastructure_only_from_composition_roots():
    """Module-scope imports of infrastructure would make the port indirection
    a fiction. Function-local ones are the wiring factories, which is where
    concretions are chosen on purpose.
    """
    assert _violations("services", ("app.infrastructure",), module_scope_only=True) == []


def test_nothing_below_the_api_layer_imports_the_api():
    for layer in ("domain", "infrastructure", "services", "core"):
        assert _violations(layer, ("app.api",)) == [], layer


def test_infrastructure_does_not_import_services():
    assert _violations("infrastructure", ("app.services",)) == []


@pytest.mark.parametrize("layer", ["domain", "services", "api"])
def test_vendor_sdks_stay_in_infrastructure(layer):
    found = []
    for name, path in _modules():
        if not name.startswith(f"app.{layer}"):
            continue
        for imported in _imports(path):
            root = imported.split(".")[0]
            if root in VENDOR_SDKS:
                found.append(f"{name} imports {imported}")
    assert found == []


def test_only_config_reads_the_environment():
    """`app.core.config` is the single place configuration is *read*.

    Scattered `os.getenv` calls are how a system ends up configured by
    something no one can find; §3 replaced them with one `Settings` object.

    Reads only — `os.getenv(...)` and `os.environ[...]`. Writing an env var
    is a different act with a different risk, and one the rule was never
    aimed at: `app.cli.quiet_third_party` sets `TQDM_DISABLE` and
    `HF_HUB_VERBOSITY` because an environment variable is the only interface
    those libraries expose, and it does so in an entrypoint, before the
    libraries are imported. That configures a dependency's behaviour, not
    this application's, and no `Settings` field could express it.

    A read is still a violation anywhere but `config`, including in the CLI.
    """
    offenders = []

    for name, path in _modules():
        if name == "app.core.config":
            continue

        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            # os.getenv(...) / os.environ.get(...) — reads.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func)
                if target in ("os.getenv", "os.environ.get"):
                    offenders.append(f"{name}: {target}()")
            # os.environ["X"] — a read unless it is the target of an assignment,
            # which `ast.Store` distinguishes for us.
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                if ast.unparse(node.value) == "os.environ":
                    offenders.append(f"{name}: os.environ[...]")

    assert offenders == []


def test_no_module_outside_cli_defines_a_main_entrypoint():
    """Argparse and `__main__` blocks belong in `app.cli`.

    Previously seven modules carried one, so importing a storage module to
    read a chunk also pulled in argument parsing and an interactive prompt.
    """
    offenders = []
    for name, path in _modules():
        if name.startswith("app.cli"):
            continue
        source = path.read_text(encoding="utf-8")
        if '__name__ == "__main__"' in source or "import argparse" in source:
            offenders.append(name)
    assert offenders == []
