"""Property-based invariants for the config-flow host parser.

Hand-picked host examples only cover the shapes someone thought of; the setup
dialog's host field accepts arbitrary text. These Hypothesis properties pin the
parser's contract across the whole input space:

* it never leaks anything but ``vol.Invalid`` (anything else surfaces as a
  generic "unknown" error in the dialog),
* every accepted host is a canonical fixed point (re-validating a stored host
  must not change the unique ID it produced), and
* every accepted host is safe to embed in ``f"{scheme}://{host}"`` and to use
  as a unique ID (parses back to the same endpoint, no whitespace, no
  uppercase, no credentials).
"""
from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from hypothesis import given, settings
from hypothesis import strategies as st

from custom_components.eveus.config_flow import _split_host_and_scheme

# Arbitrary garbage, biased toward URL-ish metacharacters so the interesting
# branches (schemes, ports, userinfo, brackets) are actually reached.
_raw_text = st.text(
    alphabet=st.one_of(
        st.characters(),
        st.sampled_from(list("./:@[]#?%-_ \t\nабв123aAzZ")),
    ),
    max_size=64,
)

# Plausible host material: hostname labels, IPv4, IPv6, with optional
# scheme/port decoration — high acceptance rate to exercise the success path.
_label = st.from_regex(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,10}[a-zA-Z0-9])?", fullmatch=True)
_hostname = st.lists(_label, min_size=1, max_size=4).map(".".join)
_ipv4 = st.tuples(*[st.integers(0, 255)] * 4).map(lambda t: ".".join(map(str, t)))
_ipv6 = st.ip_addresses(v=6).map(str)
_bare_host = st.one_of(_hostname, _ipv4, _ipv6)
_decorated_host = st.builds(
    lambda scheme, host, port: (
        (scheme or "")
        + (f"[{host}]" if ":" in host and (scheme or port) else host)
        + (port or "")
    ),
    st.sampled_from(["", "http://", "https://", "HTTP://"]),
    _bare_host,
    st.one_of(
        st.just(""),
        # The scheme-default ports get special stripping treatment, so make
        # sure the generator actually produces them.
        st.sampled_from([":80", ":443"]),
        st.integers(1, 65535).map(lambda p: f":{p}"),
    ),
)


@settings(max_examples=300)
@given(raw=st.one_of(_raw_text, _decorated_host))
def test_host_parser_only_ever_raises_vol_invalid(raw: str) -> None:
    """Any failure must be vol.Invalid — never a leaked parser exception."""
    try:
        host, scheme = _split_host_and_scheme(raw)
    except vol.Invalid:
        return
    assert isinstance(host, str) and host
    assert scheme in ("http", "https")


@settings(max_examples=300)
@given(raw=_decorated_host)
def test_accepted_host_is_a_canonical_fixed_point(raw: str) -> None:
    """Re-validating an accepted host must reproduce it exactly.

    The accepted host becomes the entry's unique ID and stored CONF_HOST;
    reconfigure/repair re-validate it, so a drifting canonical form would
    change the charger's identity and orphan its device/entities.
    """
    try:
        host, scheme = _split_host_and_scheme(raw)
    except vol.Invalid:
        return
    assert _split_host_and_scheme(f"{scheme}://{host}") == (host, scheme)


@settings(max_examples=300)
@given(raw=_decorated_host)
def test_accepted_host_is_url_and_unique_id_safe(raw: str) -> None:
    """An accepted host embeds cleanly into the poll URL and the unique ID."""
    try:
        host, scheme = _split_host_and_scheme(raw)
    except vol.Invalid:
        return

    # Unique-ID hygiene: no whitespace/control chars, no uppercase (two
    # spellings of one charger must not become two devices), no userinfo.
    assert host == host.strip()
    assert not any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in host)
    assert host == host.lower()
    assert "@" not in host

    # URL round-trip: the exact string the coordinator builds must parse back
    # to a non-empty hostname and the same in-range port.
    parsed = urlparse(f"{scheme}://{host}")
    assert parsed.hostname
    assert parsed.username is None and parsed.password is None
    if parsed.port is not None:
        assert 1 <= parsed.port <= 65535
        # The scheme-default port is stripped during validation, so it can
        # never reappear and split one endpoint into two unique IDs.
        assert parsed.port != (80 if scheme == "http" else 443)
