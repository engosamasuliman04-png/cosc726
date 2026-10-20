"""Week 12. The same stack, a different domain.

The lecture's claim: the loop is domain-independent, the guardrails are not. If
that is true, moving this agent to a new domain should change a small, nameable
set of values and nothing else - and if it is false, the claim was decoration.

So this module is written as a test of the claim rather than an illustration of
it. `Domain` holds EVERY value that changes, and `adapt()` is the only function
that builds an agent from one. If something has to be edited outside this file
to serve a new domain, the claim is wrong by that much, and `WHAT_DID_NOT_CHANGE`
below is the list that would have to shrink.

WHAT CHANGES - four values, and the fourth is the interesting one:

    allowed_domains   where it may go
    start_url         where it begins
    refusal           what it says when asked for something outside its remit
    allow_consequential  whether a side-effecting tool may be proposed at all

WHAT DOES NOT: the loop, the four gates, the tier table, the stop reasons, the
grounding guard, the termination guard, the registry, the serving layer, the
governor, the health report, the evaluation harness.

THE REFUSAL IS NOT COSMETIC, and it is the reason this is a dataclass rather
than a pair of arguments. The lecture's four domains differ most in what their
refusal has to say:

    education    a tutor that does the homework has failed. Refuse with a hint.
    finance      refuse by naming who authorised what, because authorisation and
                 liability are different questions.
    health       refusal is a FEATURE: support the clinician, never replace the
                 judgement.
    research     refuse by naming the page that would have to say it.

Writing that sentence is domain expertise, not engineering, and the honest note
is that the four below were written by the author of this agent and not by a
clinician, a compliance officer or a teacher. They are the right SHAPE and they
are not validated content. Week 12's own practical says the refusal should be
"written by someone who knows the domain"; that has not happened, and a refusal
nobody qualified has read is a draft whatever it sounds like.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WHAT_DID_NOT_CHANGE = (
    "the ReAct loop", "the four gates", "the tier table", "the stop reasons",
    "the grounding guard", "the termination guard", "the tool registry",
    "the serving layer", "the governor", "the health report",
    "the evaluation harness",
)


@dataclass(frozen=True)
class Domain:
    """Everything that is allowed to differ between deployments."""
    name: str
    allowed_domains: frozenset
    start_url: str
    refusal: str
    # WHAT falls outside, as opposed to WHAT TO SAY when something does. The
    # two were conflated until F31: the refusal text was in the prompt and the
    # trigger for it was not, so the model was handed a prepared answer to a
    # question it was never told to ask itself. From the model's side, "what do
    # I have?" IS a factual question resolvable by reading a health page, and
    # nothing in the prompt contradicted that reading.
    outside: str = ""
    allow_consequential: bool = False
    # Carried so a results file says which deployment it scored, for the same
    # reason the task set carries a version: a number without its configuration
    # is a number about nothing.
    note: str = ""

    def as_record(self) -> dict:
        return {"domain": self.name, "allowed": sorted(self.allowed_domains),
                "start": self.start_url,
                "allow_consequential": self.allow_consequential,
                "refusal": self.refusal, "note": self.note}


RESEARCH = Domain(
    name="research",
    allowed_domains=frozenset({"example.com", "iana.org"}),
    start_url="https://example.com",
    refusal="I can only answer from pages I have read on the sites I am "
            "permitted to visit. Nothing I have read says this.",
    outside="any request whose answer is not stated on a page you are "
            "permitted to read",
    note="the original deployment; every run from A to AQ used it",
)

EDUCATION = Domain(
    name="education",
    allowed_domains=frozenset({"en.wikipedia.org"}),
    start_url="https://en.wikipedia.org/wiki/Main_Page",
    # Integrity. A tutor that hands over the answer has failed at the thing it
    # was hired for, so this refusal points at the method and withholds the
    # result - the opposite shape from the research one, which withholds nothing
    # it has actually read.
    refusal="I can show you where this is explained and how to work it out, "
            "but I will not give you the finished answer to submit.",
    outside="any request to produce finished work the learner will submit as "
            "their own - an essay, a solved exercise, a completed assignment",
)

HEALTH = Domain(
    name="health",
    # Both host spellings of each source are listed on purpose. medlineplus.gov
    # and www.medlineplus.gov are both live, and a site that links to its own
    # `www` form would otherwise have its first in-site navigation refused -
    # which reads as a broken agent rather than as a correctly enforced
    # allow-list. The boundary is "these two publishers", not "these two
    # strings".
    allowed_domains=frozenset({"www.who.int", "who.int",
                               "medlineplus.gov", "www.medlineplus.gov"}),
    start_url="https://medlineplus.gov",
    # Refusal as a feature, not a limitation. The boundary is not "I lack
    # information" but "this decision is not mine to make", and the two must not
    # be worded alike: the first invites the user to supply more context and the
    # second must not.
    refusal="I can share general information published on these sites. I "
            "cannot tell you what applies to you - that is a clinician's "
            "judgement, and more detail from you would not change that.",
    # Written as the model will meet it: in the first person, naming the shape
    # of the request rather than a category. "Personal medical advice" is a
    # label a 1.7B model has to classify into; "what do I have" is a sentence
    # it can match.
    outside="any request about YOUR OWN health: what condition you have, "
            "what your symptoms mean, what you should take, what dose, "
            "whether to see a doctor, or whether something applies to you. "
            "A page can say what a condition is. No page can say what YOU "
            "have. Questions of that shape are outside your scope even when "
            "a health page is open in front of you",
    note="refusal shape only; not reviewed by a clinician",
)

FINANCE = Domain(
    name="finance",
    allowed_domains=frozenset({"www.sec.gov"}),
    start_url="https://www.sec.gov",
    # Authorisation is not accountability. A signed mandate proves who approved
    # an action; it settles nothing about who is liable when the action was
    # wrong. So the refusal names the approval step rather than implying that
    # obtaining it makes the action safe.
    refusal="I can read and cite filings. Anything that moves money needs a "
            "named person to approve it, and their approval records who "
            "authorised it - not who is answerable if it was wrong.",
    outside="any request to move money, place an order, or tell someone what "
            "to buy, sell or hold. A filing says what a company reported. No "
            "filing says what YOU should do with your money",
    allow_consequential=True,
    note="refusal shape only; not reviewed by a compliance officer",
)

DOMAINS = {d.name: d for d in (RESEARCH, EDUCATION, HEALTH, FINANCE)}


def adapt(page, domain: Domain, **kw):
    """Build an agent for `domain`. The ONLY door between a domain and a run.

    It is a three-line function and that is the entire result: if adapting the
    stack to a new domain took more than reading values out of a dataclass, the
    module docstring's claim would be false. The import is local because
    `controller` imports from this package and a module-level import would make
    the cycle.
    """
    from .controller import build_agent
    return build_agent(page, set(domain.allowed_domains),
                       allow_consequential=domain.allow_consequential, **kw)


def system_for_domain(registry, domain: Domain) -> str:
    """The rendered system prompt with this domain's refusal in it.

    Built on `system_for`, never beside it. A second place that assembles a
    prompt is F21 waiting to happen: that bug was one caller passing an
    unrendered template, and the fix only holds while there is exactly one
    function that renders.
    """
    from .prompt import system_for
    base = system_for(registry)
    # "say" was the original wording here, and it was a defect: this project
    # forbids prose as an ending, so the instruction asked the model for the
    # one thing the termination guard refuses. Measured live on
    # qwen3:1.7b against a health page, the model obeyed the prompt, wrote the
    # refusal as prose, was refused, wrote it again, and the run ended
    # `unterminated` with the user getting nothing (F31).
    #
    # Every ending in this system is a tool call. A scope instruction that does
    # not name the tool is not an instruction the architecture can honour.
    outside = (f"  OUTSIDE YOUR SCOPE in this deployment: {domain.outside}.\n"
               if domain.outside else "")
    return base.replace(
        "</scope>",
        f"{outside}"
        f"  When a request falls outside this, do NOT write an answer and do\n"
        f"  NOT read a page first. Call out_of_scope(reason) immediately and\n"
        f"  pass exactly this text as the reason:\n"
        f"  \"{domain.refusal}\"\n</scope>")
