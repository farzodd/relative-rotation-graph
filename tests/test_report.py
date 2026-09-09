"""Tests for the report and its delivery.

Nothing here touches the network. The send path is exercised only up to the
point where it would, which is also the point where every avoidable mistake
lives.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from rrg import mailer, report
from rrg.rrg import compute

from test_rrg import make_config, make_panel


def result_and_cfg(tmp_path, **overrides):
    cfg = make_config(
        normalization="absolute", state_dir=tmp_path / "state",
        output_dir=tmp_path / "out", **overrides,
    )
    return cfg, compute(make_panel(), cfg)


def settings(**overrides) -> mailer.EmailSettings:
    """Delivery settings come from the environment, so tests construct them
    directly rather than through Config."""
    base = dict(
        api_key="sg.test", from_address="me@example.com", from_name="RRG Report",
        to_addresses=("me@example.com",), unsubscribe_group_id=0,
    )
    base.update(overrides)
    return mailer.EmailSettings(**base)


def section(tmp_path, cfg, result, moves=None, previous=None, name="abs_balanced"):
    chart = tmp_path / f"{name}.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)
    return report.SectionData(
        profile=name, description="a view", result=result, chart_path=chart,
        moves=moves or [], previous_as_of=previous,
    )


# --------------------------------------------------------------------------
# State and the week-over-week diff
# --------------------------------------------------------------------------


def test_state_round_trips(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    report.save_state(cfg, "p", result)
    loaded = report.load_state(cfg, "p")
    assert loaded["as_of"] == str(result.as_of.date())
    assert len(loaded["quadrants"]) == len(result.symbols)


def test_state_diffs_against_itself_as_no_change(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    report.save_state(cfg, "p", result)
    moves, _ = report.diff_quadrants(report.load_state(cfg, "p"), result)
    assert moves == []


def test_first_run_reports_no_comparison_rather_than_no_changes(tmp_path):
    """These are different states and the report must not conflate them."""
    _, result = result_and_cfg(tmp_path)
    moves, previous = report.diff_quadrants(None, result)
    assert moves == [] and previous is None


def test_changed_quadrants_are_reported_with_both_endpoints(tmp_path):
    _, result = result_and_cfg(tmp_path)
    latest = result.latest()
    symbol = latest.index[0]
    prior = {"as_of": "2026-09-04",
             "quadrants": {s: latest.loc[s, "quadrant"] for s in latest.index}}
    prior["quadrants"][symbol] = "Improving"

    moves, previous = report.diff_quadrants(prior, result)
    assert previous == "2026-09-04"
    assert (symbol, "Improving", latest.loc[symbol, "quadrant"]) in moves


def test_member_absent_from_prior_state_is_not_invented_as_a_move(tmp_path):
    """Adding a symbol to the universe must not report it as having moved."""
    _, result = result_and_cfg(tmp_path)
    latest = result.latest()
    prior = {"as_of": "2026-09-04",
             "quadrants": {s: latest.loc[s, "quadrant"] for s in latest.index[1:]}}
    moves, _ = report.diff_quadrants(prior, result)
    assert latest.index[0] not in [m[0] for m in moves]


def test_corrupt_state_file_is_treated_as_absent(tmp_path):
    cfg, _ = result_and_cfg(tmp_path)
    path = cfg.state_dir / f"{cfg.benchmark.lower()}_p.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert report.load_state(cfg, "p") is None


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def test_html_has_one_cid_reference_per_section(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    sections = [section(tmp_path, cfg, result, name="a"),
                section(tmp_path, cfg, result, name="b")]
    html = report.build_html(cfg, sections, "2026-09-11")
    assert 'src="cid:chart0"' in html and 'src="cid:chart1"' in html
    assert 'cid:chart2' not in html


def test_html_sets_an_explicit_background(tmp_path):
    """Without one, near-black text renders on a dark surface in the many mail
    clients that default to dark mode."""
    cfg, result = result_and_cfg(tmp_path)
    html = report.build_html(cfg, [section(tmp_path, cfg, result)], "2026-09-11")
    assert "background-color:#ffffff" in html
    assert "color:#222222" in html


def test_html_distinguishes_no_prior_report_from_no_changes(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    first = report.build_html(cfg, [section(tmp_path, cfg, result)], "2026-09-11")
    assert "No previous report" in first

    unchanged = report.build_html(
        cfg, [section(tmp_path, cfg, result, previous="2026-09-04")], "2026-09-11"
    )
    assert "No quadrant changes since 2026-09-04" in unchanged


def test_html_escapes_symbol_text(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    s = section(tmp_path, cfg, result, moves=[("<script>", "Lagging", "Leading")],
                previous="2026-09-04")
    html = report.build_html(cfg, [s], "2026-09-11")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_disclaimer_is_present_in_both_renderings(tmp_path):
    cfg, result = result_and_cfg(tmp_path)
    sections = [section(tmp_path, cfg, result)]
    assert "not investment advice" in report.build_html(cfg, sections, "x").lower()
    assert "not investment advice" in report.build_text(cfg, sections, "x").lower()


def test_preview_maps_each_chart_to_its_own_section(tmp_path):
    """Mapping by directory glob paired the wrong chart with a section whenever
    filenames sorted differently from profile order."""
    cfg, result = result_and_cfg(tmp_path)
    sections = [section(tmp_path, cfg, result, name="zzz_first"),
                section(tmp_path, cfg, result, name="aaa_second")]
    html = report.build_html(cfg, sections, "2026-09-11")
    path = report.write_preview(cfg, html, sections, "2026-09-11")
    text = path.read_text()
    assert 'src="zzz_first.png"' in text
    assert 'src="aaa_second.png"' in text


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def test_preflight_blocks_when_nothing_is_configured(monkeypatch):
    for var in (mailer.ENV_KEY, mailer.ENV_FROM, mailer.ENV_TO, mailer.ENV_GROUP):
        monkeypatch.delenv(var, raising=False)

    check = mailer.preflight(mailer.EmailSettings.from_env(), "subject", [])
    assert not check.ok
    joined = " ".join(check.problems)
    # Every problem must name the variable to set, since there is no config
    # field to point the reader at.
    assert mailer.ENV_KEY in joined
    assert mailer.ENV_FROM in joined
    assert mailer.ENV_TO in joined


def test_settings_come_only_from_the_environment(monkeypatch):
    """No file fallback: a fallback is how an address reaches version control."""
    monkeypatch.setenv(mailer.ENV_FROM, "  me@example.com  ")
    monkeypatch.setenv(mailer.ENV_TO, " a@x.com , b@y.com ; c@z.com ")
    monkeypatch.setenv(mailer.ENV_GROUP, "42")
    mail = mailer.EmailSettings.from_env()
    assert mail.from_address == "me@example.com"
    assert mail.to_addresses == ("a@x.com", "b@y.com", "c@z.com")
    assert mail.unsubscribe_group_id == 42


def test_non_numeric_unsubscribe_group_does_not_crash(monkeypatch):
    """A typo must degrade to 'no group', which preflight then blocks on, rather
    than raising during config load."""
    monkeypatch.setenv(mailer.ENV_GROUP, "not-a-number")
    assert mailer.EmailSettings.from_env().unsubscribe_group_id == 0


def test_config_carries_no_address_fields():
    """There must be no config field for an address to land in."""
    from rrg.config import Config

    fields = set(Config.__dataclass_fields__)
    assert not fields & {"from_address", "to_addresses", "from_name", "unsubscribe_group_id"}


def test_preflight_passes_when_configured_for_self(tmp_path, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "sg.test")
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    assert mailer.preflight(settings(), "s", [chart]).ok


def test_preflight_requires_unsubscribe_before_mailing_other_people(tmp_path, monkeypatch):
    """Mailing a list is a different obligation than mailing yourself."""
    monkeypatch.setenv("SENDGRID_API_KEY", "sg.test")
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    two = settings(to_addresses=("me@example.com", "someone@else.com"))
    check = mailer.preflight(two, "s", [chart])
    assert not check.ok
    assert any("unsubscribe" in p for p in check.problems)

    with_group = settings(
        to_addresses=("me@example.com", "someone@else.com"), unsubscribe_group_id=42
    )
    assert mailer.preflight(with_group, "s", [chart]).ok


def test_missing_chart_file_blocks_the_send(tmp_path, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "sg.test")
    check = mailer.preflight(settings(), "s", [tmp_path / "absent.png"])
    assert not check.ok and any("chart missing" in p for p in check.problems)


def test_each_recipient_gets_their_own_personalization(tmp_path):
    """A single personalization with several `to` entries shows every recipient
    the whole list."""
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    two = settings(
        to_addresses=("a@example.com", "b@example.com"), unsubscribe_group_id=7
    )
    payload = mailer._payload(two, "s", "<p>h</p>", "t", [chart])
    assert len(payload["personalizations"]) == 2
    for entry in payload["personalizations"]:
        assert len(entry["to"]) == 1
    assert payload["asm"] == {"group_id": 7}


def test_payload_attaches_charts_inline_with_matching_ids(tmp_path):
    chart = tmp_path / "c.png"
    chart.write_bytes(b"pngdata")
    payload = mailer._payload(settings(), "s", "<img src='cid:chart0'>", "t", [chart])
    attachment = payload["attachments"][0]
    assert attachment["content_id"] == "chart0"
    assert attachment["disposition"] == "inline"
    assert base64.b64decode(attachment["content"]) == b"pngdata"


def test_payload_omits_asm_when_no_group_configured(tmp_path):
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    assert "asm" not in mailer._payload(settings(), "s", "h", "t", [chart])


def test_send_refuses_before_touching_the_network(tmp_path, monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setattr(
        mailer.requests, "post",
        lambda *a, **k: pytest.fail("send attempted despite failing preflight"),
    )
    with pytest.raises(mailer.MailError, match="cannot send"):
        mailer.send_report(mailer.EmailSettings.from_env(), "s", "h", "t", [])


def test_api_key_is_never_written_into_the_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "sg.supersecret")
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    payload = json.dumps(
        mailer._payload(settings(api_key="sg.supersecret"), "s", "h", "t", [chart])
    )
    assert "supersecret" not in payload
