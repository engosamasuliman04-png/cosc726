"""Week 12: does the domain swap actually only change the four values it claims?

The module says the loop is domain-independent and the guardrails are not. That
is a falsifiable statement, so these tests try to falsify it rather than
illustrate it.
"""

import pytest

from browser_agent import build_agent, system_for
from browser_agent.domains import (
    DOMAINS, EDUCATION, FINANCE, HEALTH, RESEARCH, Domain, adapt,
    system_for_domain,
)
from browser_agent.fakes import ALLOW, FakePage


def test_the_registry_is_identical_across_every_domain():
    """The claim, as an assertion. Same tool names, same tiers, same schemas,
    in four domains with nothing in common but this code.

    If a domain needed its own tool, the swap would not be a swap - it would be
    a second agent sharing a folder with the first.
    """
    page = FakePage("https://example.com/")
    shapes = {}
    for name, dom in DOMAINS.items():
        _, reg, _ = adapt(page, dom)
        shapes[name] = sorted(
            (n, s.tier.value, repr(s.args_model.model_json_schema()))
            for n, s in reg.items())

    first = shapes["research"]
    for name, shape in shapes.items():
        assert shape == first, f"{name} has a different registry"
    assert len(first) == 8


def test_the_allow_list_is_the_thing_that_actually_changed():
    """Not just that it differs - that it CONSTRAINS. A domain whose allow-list
    changed without changing what gate 4 permits has changed a label."""
    page = FakePage("https://example.com/")
    research, _, research_disp = adapt(page, RESEARCH)
    education, _, _ = adapt(page, EDUCATION)

    assert "example.com" in research.allowed_domains
    assert "example.com" not in education.allowed_domains
    assert "en.wikipedia.org" in education.allowed_domains
    # And the dispatcher is reading the same set, not a copy it was handed once.
    assert research_disp.t.allowed_domains is research.allowed_domains


def test_the_consequential_tier_is_per_domain_and_defaults_to_closed():
    """Finance is the only domain here that may even PROPOSE a side effect, and
    proposing is still not doing: `submit_form` ends `pending_approval` and
    submits nothing. Three of four domains cannot reach that tool at all."""
    page = FakePage("https://example.com/")
    assert adapt(page, FINANCE)[2].allow_consequential is True
    for dom in (RESEARCH, EDUCATION, HEALTH):
        assert adapt(page, dom)[2].allow_consequential is False


def test_each_refusal_reaches_the_prompt_and_only_its_own():
    page = FakePage("https://example.com/")
    for dom in DOMAINS.values():
        _, reg, _ = adapt(page, dom)
        prompt = system_for_domain(reg, dom)
        assert dom.refusal in prompt
        for other in DOMAINS.values():
            if other.name != dom.name:
                assert other.refusal not in prompt


def test_the_domain_prompt_is_still_a_rendered_prompt():
    """F21: the placeholder shipped unrendered for twenty-six runs because a
    second path assembled a prompt. `system_for_domain` builds ON `system_for`
    rather than beside it, and this is the test that keeps it that way."""
    page = FakePage("https://example.com/")
    _, reg, _ = adapt(page, HEALTH)
    prompt = system_for_domain(reg, HEALTH)

    assert "<<TOOLS>>" not in prompt
    for name in reg:
        assert name in prompt
    # Everything system_for produced is still present - the refusal is an
    # addition, not a replacement.
    assert all(line in prompt for line in system_for(reg).split("\n")
               if line.strip() and "</scope>" not in line)


def test_the_health_refusal_does_not_invite_the_user_to_supply_more_detail():
    """The one refusal where wording is a safety property rather than a style
    choice. "I need more information" and "this is not my decision" look alike
    and are opposites: the first asks the user to keep going.

    Asserted on HEALTH specifically because it is the domain where getting this
    wrong converts a boundary into a negotiation.
    """
    r = HEALTH.refusal.lower()
    assert "more detail from you would not change that" in r
    assert "clinician" in r


def test_a_new_domain_needs_no_code_outside_its_dataclass():
    """The claim's real test: invent a domain this file has never seen and build
    a working agent from it, touching nothing else."""
    invented = Domain(
        name="legal",
        allowed_domains=frozenset({"www.law.cornell.edu"}),
        start_url="https://www.law.cornell.edu",
        refusal="I can quote the text of a statute. I cannot tell you how it "
                "applies to your situation.")
    page = FakePage("https://example.com/")
    tools, reg, disp = adapt(page, invented)

    assert len(reg) == 8
    assert disp.allow_consequential is False
    assert "www.law.cornell.edu" in tools.allowed_domains
    assert invented.refusal in system_for_domain(reg, invented)


def test_the_default_build_and_the_adapted_build_agree():
    """`adapt` must be the same function the rest of the project already uses,
    not a parallel one that will drift from it."""
    page = FakePage("https://example.com/")
    _, direct, _ = build_agent(page, set(ALLOW))
    _, viaadapt, _ = adapt(page, RESEARCH)
    assert sorted(direct) == sorted(viaadapt)


def test_every_domain_records_what_it_was():
    """A results file that does not name its deployment is a number about
    nothing - the same rule the task set version exists for."""
    for dom in DOMAINS.values():
        rec = dom.as_record()
        assert rec["domain"] and rec["refusal"] and rec["start"]
        assert isinstance(rec["allowed"], list)


@pytest.mark.parametrize("dom", list(DOMAINS.values()), ids=lambda d: d.name)
def test_a_domain_is_frozen_so_a_run_cannot_edit_its_own_boundary(dom):
    """The allow-list is the outermost boundary in this project. A mutable one
    is a boundary the code inside it can move."""
    with pytest.raises(Exception):
        dom.name = "something else"
