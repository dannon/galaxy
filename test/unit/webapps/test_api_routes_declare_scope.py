"""Every route under /api carries the scope check, aliases and CORS twins included.

The allowlist for scoped tokens is exactly the set of annotated routes, which only holds if
no registration path drops the dependency. This walks the real router tree.
"""

from fastapi import FastAPI

from galaxy.webapps.base.api import include_all_package_routers


def _walk(routes):
    for route in routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        elif hasattr(route, "routes"):
            yield from _walk(route.routes)
        else:
            yield route


def _dependency_names(dependant) -> set[str]:
    names: set[str] = set()

    def collect(node):
        for sub in node.dependencies:
            names.add(getattr(sub.call, "__name__", ""))
            collect(sub)

    collect(dependant)
    return names


def test_every_api_route_checks_scope():
    app = FastAPI()
    include_all_package_routers(app, "galaxy.webapps.galaxy.api")
    unchecked = []
    seen = 0
    for route in _walk(app.routes):
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api"):
            continue
        methods = set(getattr(route, "methods", None) or [])
        if methods == {"OPTIONS"} and path == "/api/{rest_of_path:path}":
            # the catch-all CORS preflight handler answers before any credential is read
            continue
        seen += 1
        dependant = getattr(route, "dependant", None)
        if dependant is None or "check_scope" not in _dependency_names(dependant):
            unchecked.append((path, sorted(getattr(route, "methods", None) or [])))
    assert seen > 100
    assert unchecked == []
