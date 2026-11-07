"""Shared HTTP helpers that retry transient upstream failures.

Every connector reads from a third-party API over HTTPS. Before this module a
single transient failure — a 5xx from a load balancer, a connection reset, a
read timeout — aborted the whole daily run and left that source with zero rows
for the day.

That is worse than it sounds: every connector fetches *current* inventory, with
no historical query available, so a gap can only be repaired on the day it
happens. Miss it and the day is lost permanently. Retrying transient conditions
keeps a brief upstream blip from costing a day of history.

Scope: these retries cover transient *transport* failures. An upstream outage
longer than the backoff budget still fails the source, and genuine 4xx
responses (bad credentials, expired tokens) are not retried.
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 5 retries with backoff_factor 2 sleep 0s, 4s, 8s, 16s, 32s — 60s of waiting.
# Sized for momentary blips, not for sustained outages: the ShipHero outage on
# 2026-11-01 lasted hours and no sane retry budget would have covered it.
RETRY_TOTAL = 5
RETRY_BACKOFF = 2
RETRY_STATUSES = (429, 500, 502, 503, 504)

# Connector timeouts are per-request, not per-call-chain; retries multiply the
# worst case, so keep individual timeouts modest at the call sites.


def retry_policy(total: int = RETRY_TOTAL, backoff: float = RETRY_BACKOFF) -> Retry:
    return Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        status_forcelist=RETRY_STATUSES,
        # Retry POST as well as GET. Every call in this project is a read or a
        # token refresh: ShipHero and Shopify GraphQL/SOAP reads are POSTs, and
        # no connector mutates upstream state, so replaying one is safe.
        allowed_methods=None,
        backoff_factor=backoff,
        # Hand the final response back to the caller instead of raising, so
        # existing raise_for_status() calls keep producing the same errors.
        raise_on_status=False,
        respect_retry_after_header=True,
    )


def session(total: int = RETRY_TOTAL, backoff: float = RETRY_BACKOFF) -> requests.Session:
    """A requests.Session that retries transient failures on http and https."""
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry_policy(total, backoff))
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_shared: requests.Session | None = None


def _shared_session() -> requests.Session:
    global _shared
    if _shared is None:
        _shared = session()
    return _shared


def get(url: str, **kwargs) -> requests.Response:
    """requests.get with transient-failure retries."""
    return _shared_session().get(url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    """requests.post with transient-failure retries."""
    return _shared_session().post(url, **kwargs)
