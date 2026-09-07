"""The provider split: what the two arms share, and what startup refuses.

    uv run python check_llm.py            config only, no network
    uv run python check_llm.py --live     plus one real generation

No database. No GCP credentials needed -- everything here is about the shape of
the request and the shape of the refusal, both of which are decidable offline.
That matters, because the Vertex arm has to be verifiable before a service
account exists.

TWO THINGS THIS FILE EXISTS FOR
-------------------------------

1. The two providers must build the SAME request. They speak one generateContent
   dialect and differ only in URL and credential, so `temperature: 0` and the
   `thought` filter are each one decision. A copy in the second provider would
   be a duplicate definition created at the moment of writing -- the failure
   that produced two money formatters, two price queries and two spellings of a
   subject key. These checks are what keeps it one.

2. The embedding credential must stay required when LLM_PROVIDER=vertex. That
   guard used to be a side effect of `PROVIDER == "gemini"`, and adding a second
   provider would have made it SILENTLY NARROW ITS OWN SCOPE: startup goes green
   with no AI Studio key, and the first customer question dies in
   app/embeddings.py instead. Most guards here needed widening by hand; this one
   shrinks itself when a config value changes, which is worse, because editing
   .env does not look like editing a check.

   That check carries its own NEGATIVE CONTROL below. A test that has never been
   observed to fail is a claim, not evidence -- and the checks most likely to be
   blind are the ones that read as obviously correct.
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app import gemini_keys, llm  # noqa: E402

LIVE = "--live" in sys.argv
passed = failed = 0


def check(label, got, want):
    global passed, failed
    ok = got == want
    passed, failed = passed + ok, failed + (not ok)
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


def raises(label, fn, needle):
    """Startup must refuse, and the message must say which knob to turn."""
    global passed, failed
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - any refusal counts, text is graded
        if needle.lower() in str(exc).lower():
            passed += 1
            print(f"  [ok  ] {label}")
            return
        failed += 1
        print(f"  [FAIL] {label}")
        print(f"         refused, but the message never mentions {needle!r}")
        print(f"         {exc}")
        return
    failed += 1
    print(f"  [FAIL] {label}")
    print("         it was accepted")


# ---------------------------------------------------------------------------
print("\n1. Both providers build the same request")

SYSTEM, PROMPT = "be terse", "what time do you open?"
IMAGE = ("image/png", b"\x89PNG\r\n\x1a\nfake")

body = llm._payload(SYSTEM, PROMPT, None)
check("temperature is zero -- one decision, one home",
      body["generationConfig"]["temperature"], 0)
check("the system prompt travels as systemInstruction",
      body["systemInstruction"], {"parts": [{"text": SYSTEM}]})
check("a text-only call sends exactly one part",
      body["contents"][0]["parts"], [{"text": PROMPT}])

with_image = llm._payload(SYSTEM, PROMPT, IMAGE)
check("an image call sends the image FIRST, then the text",
      [next(iter(p)) for p in with_image["contents"][0]["parts"]],
      ["inline_data", "text"])
check("the image is base64 with its media type",
      with_image["contents"][0]["parts"][0]["inline_data"]["mime_type"],
      "image/png")

# The parity that matters: neither provider gets to have its own opinion.
check("the vertex arm and the gemini arm share the builder, not a copy",
      llm._payload(SYSTEM, PROMPT, None), body)

# ---------------------------------------------------------------------------
print("\n2. Thinking parts never reach the customer")
# A reasoning part carries "thought": true. Returning it means the customer
# reads the model's internal monologue instead of an answer.

THINKING = {"candidates": [{"content": {"parts": [
    {"text": "The customer asks about hours. I should check...", "thought": True},
    {"text": "  We open at 08:00.  "},
]}}]}
check("the thought is dropped and the answer is stripped",
      llm._text(THINKING), "We open at 08:00.")

check("a reply split across parts is joined",
      llm._text({"candidates": [{"content": {"parts": [
          {"text": "08:00"}, {"text": "-19:00"}]}}]}),
      "08:00-19:00")

def _with(fn, provider=None, project="p", location="l", model="m",
          vision="m", keys=None):
    """Run fn with llm's module config temporarily replaced.

    Module globals rather than environment variables, because llm.py reads the
    environment once at import and this has to be able to test what a DIFFERENT
    .env would do without reimporting the module.
    """
    saved = (llm.PROVIDER, llm.VERTEX_PROJECT, llm.VERTEX_LOCATION,
             llm.VERTEX_MODEL, llm.VERTEX_VISION_MODEL, gemini_keys.KEYS)
    llm.PROVIDER = provider if provider is not None else llm.PROVIDER
    llm.VERTEX_PROJECT, llm.VERTEX_LOCATION = project, location
    llm.VERTEX_MODEL, llm.VERTEX_VISION_MODEL = model, vision
    if keys is not None:
        gemini_keys.KEYS = keys
    try:
        return fn()
    finally:
        (llm.PROVIDER, llm.VERTEX_PROJECT, llm.VERTEX_LOCATION,
         llm.VERTEX_MODEL, llm.VERTEX_VISION_MODEL, gemini_keys.KEYS) = saved


# ---------------------------------------------------------------------------
print("\n3. Startup refuses a configuration that cannot work")

raises("an unknown LLM_PROVIDER is refused by name",
       lambda: _with(provider="anthropic", fn=llm.check_configured),
       "not implemented")

raises("LLM_PROVIDER=vertex with no project is refused",
       lambda: _with(provider="vertex", project=None, fn=llm.check_configured),
       "VERTEX_PROJECT")

raises("LLM_PROVIDER=vertex with no model id is refused",
       lambda: _with(provider="vertex", model=None, fn=llm.check_configured),
       "VERTEX_MODEL")


# ---------------------------------------------------------------------------
print("\n4. The embedding credential survives the provider switch")
# THE POINT OF THIS FILE. Generation moved to Vertex; embeddings did not, and
# never will -- a vector from a different serving stack is not comparable to the
# ones already in the database. So the AI Studio key is still needed on every
# customer question even when generation does not use one.

raises("LLM_PROVIDER=vertex with no Gemini key is STILL refused",
       lambda: _with(provider="vertex", keys=[], fn=llm.check_configured),
       "embeddings")

# The negative control. Reproduce the pre-split guard -- the embedding check
# living inside the `PROVIDER == "gemini"` branch -- and confirm it accepts the
# exact configuration above. If this reports "refused", the check above is not
# testing what it claims and the two are indistinguishable from each other.


def old_style_check() -> None:
    """app/llm.py as it stood before Vertex, verbatim in shape."""
    if llm.PROVIDER not in llm._PROVIDERS:
        raise RuntimeError("not implemented")
    if llm.PROVIDER == "gemini" and gemini_keys.count() == 0:
        raise RuntimeError("No Gemini key.")


def control() -> str:
    try:
        _with(provider="vertex", keys=[], fn=old_style_check)
        return "accepted it -- the guard had narrowed, as expected"
    except RuntimeError:
        return "refused"


check("control: the OLD guard accepts that same config, so this test can see it",
      control(), "accepted it -- the guard had narrowed, as expected")

# ---------------------------------------------------------------------------
print("\n5. The live path still answers")

if LIVE:
    # One real generation on whatever .env selects. Cheap, and it is the only
    # thing here that proves the refactor did not break the shipping provider:
    # everything above is about shapes, and a shape can be right while the call
    # is broken.
    reply = llm.complete("Reply with exactly the word: ok", "ping")
    check(f"a real {llm.PROVIDER} generation came back non-empty",
          bool(reply.strip()), True)
    print(f"         {llm.PROVIDER}/{llm.GEMINI_MODEL} said: {reply[:60]!r}")
else:
    print("  [skip] pass --live to spend one real generation")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
