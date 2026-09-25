"""The Ollama client: two paths, decided at runtime.

`FakeTransport` answers /api/show and /api/chat from a script, so none of this
needs a server. The live counterpart is scripts/run_agent.py.
"""

import pytest

from browser_agent import SYSTEM, ToolCall, build_agent, report, run_agent
from browser_agent.fakes import ALLOW, FakePage, FakeTransport, native, prose
from browser_agent.ollama_client import (
    OllamaBackend, OllamaClient, OllamaError, extract_json,
)


def fresh():
    return build_agent(FakePage("https://example.com/"), ALLOW)


# ---------------------------------------------- capability detection
@pytest.mark.parametrize("caps,expected", [
    (["completion", "tools"],   True),
    (["completion"],            False),
    (["completion", "vision"],  False),
])
def test_tool_calling_is_a_template_property_not_a_size_property(caps, expected):
    b = OllamaBackend("fake", FakeTransport(caps, []))
    assert b.supports_tools() is expected


def test_a_missing_model_raises_instead_of_reporting_no_tools():
    """An earlier version had a bare `except` here and returned []. A model that
    did not exist reported "tool calling: False", which looks like a legitimate
    answer and sends you to the next step with a wrong conclusion.

    A swallowed error is worse than a loud one: it produces a number that looks right.
    """
    class Missing:
        def post(self, path, payload):
            raise OllamaError("model not found")

    with pytest.raises(OllamaError):
        OllamaBackend("nope", Missing()).capabilities()


# ---------------------------------------------- path A: native
def test_native_path_sends_schemas_and_parses_nothing():
    _, reg, _ = fresh()
    t = FakeTransport(["completion", "tools"], [native("read_page", {}, "observe first")])
    c = OllamaClient(OllamaBackend("qwen3:4b", t))
    r = c.complete(SYSTEM, [{"role": "user", "content": "q"}], reg)

    assert c.path == "native"
    assert r.tool_call.name == "read_page"
    assert r.tool_call.thought == "observe first"
    assert len(t.seen_tools) == len(reg)          # derived from the registry
    assert c.parse_failures == 0


def test_arguments_may_arrive_as_a_json_string():
    """Some builds send `arguments` as a string rather than an object."""
    _, reg, _ = fresh()
    c = OllamaClient(OllamaBackend("qwen3:4b",
        FakeTransport(["completion", "tools"], [native("click_link", '{"index": 0}')])))
    r = c.complete(SYSTEM, [{"role": "user", "content": "q"}], reg)
    assert r.tool_call.args == {"index": 0}


@pytest.mark.parametrize("reply,expected", [
    (native("download_pdf", {"url": "x"}),   "unknown_tool"),
    (native("click_link", {"index": "first"}), "schema_violation"),
])
async def test_native_is_not_immune_to_the_gates(reply, expected):
    """Native tool calling does not remove the need for gates. A tool-capable
    model can still invent a tool or send the wrong type.
    """
    _, reg, disp = fresh()
    c = OllamaClient(OllamaBackend("qwen3:4b",
                                   FakeTransport(["completion", "tools"], [reply])))
    r = c.complete(SYSTEM, [{"role": "user", "content": "q"}], reg)
    obs, _ = await disp.dispatch(r.tool_call)
    assert obs["error"] == expected


# ---------------------------------------------- path B: prose
@pytest.mark.parametrize("raw,ok", [
    ('{"thought":"s","tool":"read_page","args":{}}',                       True),
    ('Sure!\n```json\n{"thought":"s","tool":"read_page","args":{}}\n```',   True),
    ('I should look first.\n{"thought":"s","tool":"read_page","args":{}}\nok!', True),
    ("{'thought':'s','tool':'read_page','args':{}}",                       True),
    ('{"thought":"s","tool":"read_page","args":{},}',                      True),
    ('{"thought":"x","tool":"click_link","args":{"index":0,"new":True}}',  True),
    ('I will now read the page and tell you what it says.',                False),
    ('',                                                                   False),
])
def test_extract_json_handles_what_small_models_emit(raw, ok):
    obj, err = extract_json(raw)
    assert (obj is not None) is ok


def test_prose_path_retries_with_the_error_as_feedback():
    _, reg, _ = fresh()
    t = FakeTransport(["completion"], [
        prose("I will read the page."),                                   # no json
        prose('```json\n{"thought":"observe","tool":"read_page","args":{}}\n```'),
    ])
    c = OllamaClient(OllamaBackend("some-1.5b", t), max_retries=2)
    r = c.complete(SYSTEM, [{"role": "user", "content": "q"}], reg)

    assert c.path == "prose"
    assert c.attempts == 2 and c.parse_failures == 1
    assert r.tool_call.name == "read_page"
    assert t.seen_tools is None        # the list went INTO the prompt, not a tools array


async def test_an_invented_tool_never_reaches_the_world():
    """Constrained decomposition, enforced rather than requested. After the retry
    budget the client returns a call gate 1 refuses - never a crash.
    """
    _, reg, disp = fresh()
    t = FakeTransport(["completion"],
                      [prose('{"thought":"a","tool":"download_pdf","args":{}}')] * 3)
    c = OllamaClient(OllamaBackend("m", t), max_retries=2)
    r = c.complete(SYSTEM, [{"role": "user", "content": "q"}], reg)

    assert r.tool_call.name == "__unparsable__"
    obs, _ = await disp.dispatch(r.tool_call)
    assert obs["error"] == "unknown_tool"


# ---------------------------------------------- end to end
async def test_full_run_on_the_native_path():
    _, reg, disp = fresh()
    t = FakeTransport(["completion", "tools"], [
        native("read_page", {}, "observe the start page"),
        native("list_links", {}, "find the authority link"),
        native("click_link", {"index": 0}, "follow it"),
        native("read_page", {}, "read the reservation text"),
        native("finish", {"answer": "RFC 2606 reserves example.com, .net and .org",
                          "evidence_url": "https://www.iana.org/help/example-domains"},
               "answer with evidence"),
    ])
    c = OllamaClient(OllamaBackend("qwen3:4b", t))
    res = await run_agent(c, disp, reg, SYSTEM, "What does RFC 2606 reserve?", max_steps=8)

    assert res.stop_reason == "complete"
    assert c.parse_failures == 0
    assert len(res.evidence) == 1
    assert all(t_["obs"].get("ok") for t_ in res.trace)
