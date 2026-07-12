"""First-party test fixture installed into site-packages (never in-tree source).

Its __init__ eagerly imports a sibling, reproducing the model-gateway shape:
importing the package runs this line, which pulls in siblings the entry function
never references directly. A bundler that shipped only a hollow shell would omit
them and the remote job would die with ModuleNotFoundError.

Driven by tests/integration/cortexflow/test_remote_fixture.py.
"""

from remote_fixture.core import ingest

__all__ = ["ingest"]
