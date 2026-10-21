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


def test_health_lists_both_host_spellings_of_each_publisher():
    """medlineplus.gov and www.medlineplus.gov are both live hosts of the same
    publisher. Listing only one means the site's first link to its own `www`
    form is refused - which looks like a broken agent and is really a
    mis-stated boundary.

    The boundary this project intends is "these two publishers", not "these
    two strings", and this test is what keeps the data saying so.
    """
    health = DOMAINS["health"]
    for host in ("medlineplus.gov", "www.medlineplus.gov",
                 "who.int", "www.who.int"):
        assert host in health.allowed_domains, host


def test_health_admits_nothing_beyond_those_two_publishers():
    """The companion to the test above: widening a boundary for convenience is
    how an allow-list stops being one."""
    publishers = {"medlineplus.gov", "who.int"}
    for host in DOMAINS["health"].allowed_domains:
        root = ".".join(host.split(".")[-2:])
        assert root in publishers, host


@pytest.mark.parametrize("dom", list(DOMAINS.values()), ids=lambda d: d.name)
def test_the_scope_instruction_names_the_tool_it_requires(dom):
    """The defect this test exists for (F31): the scope instruction said
    "say exactly this", while the controller refuses any run that ends in
    prose. The prompt asked the model for the one thing the termination guard
    rejects, and the model obeyed the prompt.

    Every ending in this system is a tool call, so a scope instruction that
    does not name a terminal tool is an instruction the architecture cannot
    honour. This test is cheap and would have caught it before a live run did.
    """
    from browser_agent.fakes import FakePage
    _, registry, _ = adapt(FakePage("https://example.com/"), dom)
    prompt = system_for_domain(registry, dom)

    head, _, tail = prompt.partition("<scope>")
    scope, _, _ = tail.partition("</scope>")

    assert "out_of_scope" in scope, (
        "the scope section must name the tool that ends an out-of-scope run")
    assert dom.refusal in scope, "the domain's own refusal text must be in it"
    assert "say exactly" not in scope.lower(), (
        "'say' asks for prose, and prose cannot end a run here")


@pytest.mark.parametrize("dom", list(DOMAINS.values()), ids=lambda d: d.name)
def test_a_domain_states_what_is_outside_it_not_only_what_to_say(dom):
    """F31's deeper half. The prompt carried the refusal TEXT and not the
    CONDITION that triggers it, so the model was handed a prepared answer to a
    question it was never told to ask itself.

    From a model's side, "what do I have?" is a factual question resolvable by
    reading a health page, and nothing in the prompt contradicted that reading.
    A refusal nobody knows when to use is a refusal that is never used.
    """
    from browser_agent.fakes import FakePage
    _, registry, _ = adapt(FakePage("https://example.com/"), dom)
    scope = system_for_domain(registry, dom).partition("<scope>")[2] \
                                            .partition("</scope>")[0]

    assert dom.outside, f"{dom.name} declares no out-of-scope condition"
    assert dom.outside in scope, "the condition must reach the prompt"
    # Checked on the whitespace-collapsed, case-folded text: the instruction is
    # wrapped across lines in the prompt, and a test that breaks on a line wrap
    # tests the formatter rather than the instruction.
    flat = " ".join(scope.split()).lower()
    assert "not read a page first" in flat, (
        "an out-of-scope request must not be answered after a read either - "
        "reading first is how F31's run reached three attempts")


def test_health_names_the_request_shape_a_small_model_can_match():
    """A 1.7B model classifies poorly and matches well. "Personal medical
    advice" is a category it must reason about; "what condition you have" is a
    sentence it can recognise. This is a prompt-design decision, and it is
    recorded as a test so it is not silently reverted into a tidier abstraction.
    """
    outside = DOMAINS["health"].outside.lower()
    for phrase in ("what condition you have", "what your symptoms mean",
                   "what dose", "whether to see a doctor"):
        assert phrase in outside, phrase


def test_raising_one_truncation_without_the_other_changes_nothing():
    """F30's trap. `read_page` caps the page text; `as_sent` caps the whole
    serialised observation, JSON wrapper included. Raise the first alone and the
    model still sees ~800 characters minus the wrapper - the change looks
    applied, is measured, and does nothing.

    Two caps in two files is how an injected payload was cut twice while the
    harness reported 0% compliance. This test is the thing that says so out loud.
    """
    from browser_agent.fakes import FakePage
    from browser_agent.tools import BrowserTools
    from browser_agent.ollama_client import as_sent

    page = FakePage("https://example.com/")
    tools = BrowserTools(page, {"example.com"}, max_text=4000)
    assert tools.max_text == 4000

    obs = {"ok": True, "url": "https://example.com/", "title": "t",
           "text": "x" * 4000}
    assert len(as_sent(obs)) == 800, "the second cap is still 800 by default"
    assert len(as_sent(obs, 4400)) > 800, "and it has to be raised too"


def test_the_read_cap_defaults_to_the_measured_value():
    """Every number in REPORT.md was taken at MAX_TEXT. A default that drifts
    makes the published figures unreproducible without anything saying so -
    which is exactly F33, and it cost a revert."""
    from browser_agent.fakes import FakePage
    from browser_agent.tools import BrowserTools, MAX_TEXT
    from browser_agent.controller import build_agent
    assert MAX_TEXT == 800
    assert BrowserTools(FakePage("https://example.com/"), set()).max_text == 800
    tools, _, _ = build_agent(FakePage("https://example.com/"), {"example.com"})
    assert tools.max_text == 800


def test_a_rebuilt_domain_carries_every_field_the_prompt_depends_on():
    """F36. Both interactive scripts rebuild the Domain to fold in --allow and
    --start, and both listed the fields by hand. `outside` was added later and
    neither list was updated, so the out-of-scope CONDITION silently stopped
    reaching the prompt while the refusal TEXT still did.

    Three measured attempts to make the agent call out_of_scope were then run
    against a prompt that did not contain the fix, and the conclusion drawn -
    'the limit is the model, not the instruction' - was drawn from runs that
    never carried the instruction.

    A hand-written field list is a silent dropper by construction: adding a
    field to the dataclass cannot make the copies fail. This test is what makes
    it fail.
    """
    import dataclasses
    from browser_agent.fakes import FakePage

    base = DOMAINS["health"]
    rebuilt = dataclasses.replace(base, allowed_domains=frozenset({"x.test"}))

    for f in dataclasses.fields(Domain):
        if f.name == "allowed_domains":
            continue
        assert getattr(rebuilt, f.name) == getattr(base, f.name), (
            f"{f.name} was lost when the domain was rebuilt")

    _, reg, _ = adapt(FakePage("https://x.test/"), rebuilt)
    scope = system_for_domain(reg, rebuilt).partition("<scope>")[2] \
                                           .partition("</scope>")[0]
    assert base.outside in scope, "the out-of-scope condition must survive"
    assert base.refusal in scope, "and so must the refusal text"
