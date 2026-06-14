"""Unit tests for route_template (observability.middleware).

Locks the Starlette-1.0 fix: route.path is relative to the outermost
prefixed router (the "/v1" is dropped and not moved to root_path), so the
full template must be reconstructed from the real request path. These are
pure-function tests with synthetic request scopes - no app or stack needed.
"""

from types import SimpleNamespace

from sheaf.observability.middleware import route_template


def _req(route_path: str | None, scope_path: str):
    scope: dict = {"path": scope_path}
    if route_path is not None:
        scope["route"] = SimpleNamespace(path=route_path)
    return SimpleNamespace(scope=scope, url=SimpleNamespace(path=scope_path))


def test_restores_dropped_v1_prefix():
    # Starlette 1.0 reports "/import/prism/preview" for a route mounted at
    # /v1/import/prism/preview; the helper must restore the full path.
    assert (
        route_template(_req("/import/prism/preview", "/v1/import/prism/preview"))
        == "/v1/import/prism/preview"
    )


def test_keeps_path_params_templated():
    # The real path carries a concrete id; the reconstructed template must
    # keep the {id} placeholder so metric/bucket cardinality stays bounded.
    assert (
        route_template(_req("/members/{id}", "/v1/members/abc-123"))
        == "/v1/members/{id}"
    )


def test_already_full_template_unchanged():
    assert (
        route_template(_req("/v1/auth/config", "/v1/auth/config"))
        == "/v1/auth/config"
    )


def test_unmatched_route_returns_placeholder():
    assert route_template(_req(None, "/whatever")) == "<unmatched>"


def test_root_path():
    assert route_template(_req("/", "/")) == "/"
