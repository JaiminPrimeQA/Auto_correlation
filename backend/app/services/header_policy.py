"""Which request headers a generated JMeter plan actually sends.

Single source of truth for the JMX builder (which drops these headers) and
for everything that proposes correlation targets: a value can only be
correlated INTO a location the plan will send. Echo/debug endpoints return
the client's own Host and User-Agent in their responses, and without this
rule auto-correlation "found" those and then failed JMX generation.
"""

from __future__ import annotations

from ..domain.enums import LocationType
from ..domain.models import ValueOccurrence

# Transport/runtime headers that must never be replayed verbatim. JMeter/its
# HTTP client recomputes Content-Length/Host; the Cookie Manager owns cookies;
# Postman-Token and Accept-Encoding are client runtime noise.
NON_REPLAYED_HEADERS = frozenset({
    "content-length",
    "connection",
    "host",
    "transfer-encoding",
    "cookie",
    "postman-token",
    "accept-encoding",
})

# Dropped unless the tester explicitly keeps it (BuildOptions.keep_user_agent);
# it is the client's own identity, never a value produced by the server.
USER_AGENT = "user-agent"


def is_correlation_target(sink: ValueOccurrence) -> bool:
    """False for request headers the generated plan does not send as recorded."""
    if sink.location_type != LocationType.HEADER:
        return True
    name = (sink.key or sink.canonical_path).lower()
    return name not in NON_REPLAYED_HEADERS and name != USER_AGENT
