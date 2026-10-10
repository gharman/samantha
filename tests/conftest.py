"""Keep the pytest process itself from arming a Samantha resident.

`import samantha` arms a resident in the importing process. Under pytest that
is the test runner, and with a developer's .env in the repository root it would
start real cognition (and model calls) during the tests. The tests that need a
resident start their own subprocess with an explicit environment.
"""

import os

_saved = os.environ.get("SAMANTHA")
os.environ["SAMANTHA"] = "off"
import samantha  # noqa: E402,F401  (imported once, unarmed)

if _saved is None:
    del os.environ["SAMANTHA"]
else:
    os.environ["SAMANTHA"] = _saved
