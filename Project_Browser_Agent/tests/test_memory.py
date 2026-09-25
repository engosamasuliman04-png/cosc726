"""Week 6: scope isolation, the write path, forgetting, consolidation."""

from browser_agent import ToolCall, build_agent
from browser_agent.fakes import ALLOW, FakePage
from browser_agent.memory import (
    MemoryStore, MemoryTools, Scope, WriteRejected,
    propose_procedure, register_memory_tools, review_procedure,
)

import pytest

TRUSTED = {"example.com", "iana.org"}
A, B = Scope("cust-A"), Scope("cust-B")
IANA = "https://www.iana.org/help/example-domains"


# ------------------------------------------------------- the write path
@pytest.mark.parametrize("kwargs,code", [
    (dict(kind="semantic", scope=None, text="x" * 10, when=0,
          provenance=["https://example.com/"]),                       "no_scope"),
    (dict(kind="semantic", scope=A, text="example.com is reserved",
          when=0, provenance=[]),                                     "no_provenance"),
    (dict(kind="procedural", scope=A, text="always click first",
          when=0, provenance=["ep-1"], status="active"),              "unreviewed_procedure"),
])
def test_write_gates(kwargs, code):
    st = MemoryStore(TRUSTED)
    with pytest.raises(WriteRejected) as e:
        st.write(**kwargs)
    assert e.value.code == code


def test_two_user_isolation():
    """Ten lines, and the difference between a lab exercise and a
    data-protection incident.
    """
    st = MemoryStore(TRUSTED)
    st.write("semantic", A, "RFC 2606 reserves example.com", when=0, provenance=[IANA])
    st.write("semantic", B, "B prefers the Arabic version", when=0,
             provenance=["https://example.com/prefs"])

    assert len(st.read(A, "what does RFC 2606 reserve", now=1)) == 1
    assert len(st.read(B, "what does RFC 2606 reserve", now=1)) == 0


def test_read_without_scope_raises():
    st = MemoryStore(TRUSTED)
    with pytest.raises(WriteRejected):
        st.read(None, "anything", now=0)


# ------------------------------------------------------- forgetting
def test_supersession_leaves_one_current_answer():
    st = MemoryStore(TRUSTED)
    old = st.write("semantic", A, "The reserved list is example.com only",
                   when=0, provenance=[IANA])
    st.write("semantic", A, "The reserved list is example.com, .net and .org",
             when=40, provenance=[IANA], supersedes=old.id)

    hits = st.read(A, "reserved list", now=40)
    assert len(hits) == 1
    assert ".org" in hits[0][1].text          # the new fact, not the old one


def test_temporal_decay():
    st = MemoryStore(TRUSTED)
    st.write("semantic", A, "The IANA page lists reserved example domains",
             when=0, provenance=[IANA])
    scores = [st.read(A, "reserved example domains", now=d)[0][0] for d in (0, 30, 90)]
    assert scores == sorted(scores, reverse=True)
    assert scores[1] == pytest.approx(scores[0] / 2, rel=0.01)   # 30-day half-life


# ------------------------------------------------------- consolidation
def test_nothing_promotes_itself_to_standing_policy():
    """Without a REVIEWED step, an agent promotes a one-off workaround into
    standing policy and then follows it forever, citing itself.
    """
    st = MemoryStore(TRUSTED)
    for i, day in enumerate([1, 8, 15]):
        st.write("episodic", A, f"run {i+1}: searched for registry data, found none",
                 when=day, provenance=[f"https://example.com/?run={i}"],
                 outcome="blocked_no_registry_data")

    props = propose_procedure(st, A, now=16)
    assert len(props) == 1 and props[0].status == "candidate"

    q = "recurring outcome adjust strategy"
    assert st.read(A, q, now=16, kind="procedural") == []      # invisible while candidate
    review_procedure(st, A, props[0].id, approved=True)
    assert len(st.read(A, q, now=16, kind="procedural")) == 1  # visible after a human says so


def test_consolidation_needs_a_minimum_of_support():
    st = MemoryStore(TRUSTED)
    st.write("episodic", A, "one-off", when=1, provenance=["https://example.com/"],
             outcome="something_odd")
    assert propose_procedure(st, A, now=2) == []


# ------------------------------------------------------- agentic RAG
async def test_memory_tools_inherit_the_gates():
    """Two registry entries, and they inherit gate 1, gate 2 and the tier check
    without a line of new validation.
    """
    store = MemoryStore(TRUSTED)
    store.write("semantic", A, "RFC 2606 reserves example.com, .net and .org",
                when=0, provenance=[IANA])
    _, registry, disp = build_agent(FakePage("https://example.com/"), TRUSTED)
    register_memory_tools(registry, MemoryTools(store, A, now=1))

    bad, _ = await disp.dispatch(ToolCall("search_memory", {"query": ""}))
    assert bad["error"] == "schema_violation"

    ok, tier = await disp.dispatch(
        ToolCall("search_memory", {"query": "what does RFC 2606 reserve"}))
    assert ok["ok"] and ok["count"] == 1 and tier.value == "read"


def test_recall_costs_context():
    """The window is zero-sum: every recalled fact displaces something else."""
    st = MemoryStore(TRUSTED)
    for i in range(5):
        st.write("semantic", A, f"Fact {i}: the IANA page lists reserved domains, case {i}",
                 when=0, provenance=[IANA])
    sizes = [sum(len(r.text) for _, r in st.read(A, "reserved domains", now=1, k=k))
             for k in (1, 3, 5)]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]
