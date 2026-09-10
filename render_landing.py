"""Render the landing page from design-tool source to static HTML.

    uv run python render_landing.py

    prototype/Landing Page.dc.html  ->  site/index.html

WHY THIS EXISTS RATHER THAN COPYING THE FILE. The .dc.html is not plain HTML.
It carries a 69 KB design-tool runtime (support.js), 45 `{{ }}` bindings, seven
`<sc-for>` loops and an `<sc-if>`, and the page's actual content -- the pricing
plans, the four Telegram exchanges, the demo questions, the audience list --
lives as DATA inside a `<script type="text/x-dc">` block. Served as-is it would
ship that runtime to production and render everything client-side: a blank flash
on load and nothing in the markup for a search engine or a link preview, on the
one page whose job is to be found.

So it is rendered ONCE, here, by a real browser, and the DOM that results is
saved as ordinary HTML.

WHAT THIS DOES NOT DO, AND MUST NOT
It does not invent, complete or improve anything. The page's whole argument is
that its screenshots and conversations are genuine; they are not yet, and
substituting something plausible is exactly what it was designed not to do. The
four Telegram exchanges, the checkerboard QR and the three dashed screenshot
frames all pass through verbatim -- they are markup and CSS, and this script
only reads them. There is an assertion at the bottom that they survived.
"""

import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

SOURCE = pathlib.Path("prototype/Landing Page.dc.html").resolve()
OUT = pathlib.Path("site/index.html")

# THE DOUBLE _bot IS REAL. The username is avisenamed_bot_bot, not
# avisenamed_bot -- and this is not a cosmetic distinction:
#
#   t.me/avisenamed_bot      -> "Avisena Medical Texnikum bot"   SOMEBODY ELSE'S
#   t.me/avisenamed_bot_bot  -> "avisenamed_bot"                 ours
#
# The first render shipped the single-_bot form, because the doubled suffix
# reads as a typo and "correcting" it produced a valid link to a stranger's bot.
# Seven links on a live landing page pointed at it. verify_bot() below fetches
# the profile and refuses to render unless the title matches, so a plausible
# username belonging to someone else cannot pass again.
BOT_USERNAME = "avisenamed_bot_bot"
BOT_TITLE = "avisenamed_bot"          # what t.me shows for that username
DEMO_BOT = f"https://t.me/{BOT_USERNAME}"

# Every href in the source is "#" or a prototype artboard. Matched on the link's
# visible text, because that is the only stable handle the design gives us --
# there are no ids or classes to target.
#
# Everything points at @avisenamed_bot rather than @talkwisp_demo_bot: the demo
# bot has no `business` row, so bot.py refuses to start for its token and
# nothing polls it. A main CTA that opens a silent bot is worse than no button.
# @avisenamed_bot is live with 138 real facts and answers in three scripts.
LINKS = {
    "Book a demo": f"{DEMO_BOT}?start=demo_hero",
    "Book a demo — we'll message you on Telegram": f"{DEMO_BOT}?start=demo_hero",
    # The source label says "Open @talkwisp_bot". That is the PLATFORM bot --
    # it signs web logins and has no public surface -- so the href points at the
    # agent bot instead, and the visible text has to follow or the button reads
    # as one bot and opens another. "Try the bot" is deliberately generic: it
    # survives the switch to a real demo bot without another copy edit.
    "Open @talkwisp_bot": DEMO_BOT,
    "Sign up": "/app/",
    "Or sign up and build one yourself →": "/app/",
    "info@talkwisp.uz": "mailto:info@talkwisp.uz",
    "Privacy policy": "/privacy",
    "Terms of service": "/terms",
    "Data deletion": "/data-deletion",
}

# The footer's @talkwisp_bot handle is REMOVED, not repointed. That is the
# platform bot -- it signs web logins and has no public surface. A contact
# handle nobody should message is worse than none.
REMOVE_TEXT = {"@talkwisp_bot"}

# Body copy that has to change because its destination did. The only text this
# script touches, and only where leaving it would make the page lie.
RELABEL = {"Open @talkwisp_bot": "Try the bot"}

# The three example-question chips ship as href="#" -- dead controls on a live
# page. They point at the bot: tapping a question you can see answered and
# landing where you can ask it is the natural move, and more honest than making
# them decorative.
CHIP_TEXTS = [
    "«Сколько стоит УЗИ брюшной полости?»",
    "“Shanba kuni ishlaysizmi?”",
    "“How much is a consultation?”",
]

# The switch highlights the selected language; it does not translate anything
# yet. Rendered statically the three buttons become dead controls, which is
# worse than one that half-works -- so the highlight is reimplemented here.
# Deliberately does NOT pretend to translate.
LANG_JS = """
document.querySelectorAll('[data-lang]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var group = btn.parentElement;
    group.querySelectorAll('[data-lang]').forEach(function (other) {
      var on = other === btn;
      other.style.background = on ? '#fff' : 'transparent';
      other.style.color = on ? '#14181f' : '#5c6675';
      other.style.boxShadow = on ? '0 1px 2px rgba(20,24,31,0.12)' : 'none';
    });
  });
});
"""

# Runs inside the page once the runtime has rendered. Everything it changes is
# structural -- links, hover states, fonts, scripts. No text is touched.
TRANSFORM = """
(config) => {
  // 1. HOVER STATES, AND THIS IS THE SUBTLE ONE.
  //
  //    The source carries `style-hover` attributes. The runtime consumes them
  //    and inserts real CSS rules through the CSSOM -- sheet.insertRule --
  //    against a <style> element it leaves EMPTY in the markup. So the rules
  //    exist in the live page and are invisible to outerHTML: the first render
  //    produced a page where every button had lost its hover state, silently,
  //    and the only sign was a "0 hover rules" line that was easy to read as
  //    "there were none to convert".
  //
  //    Reading them back out of document.styleSheets captures whatever the
  //    runtime actually generated, rather than this script guessing at it from
  //    the source attributes.
  const rules = [];
  for (const sheet of document.styleSheets) {
    let cssRules;
    try { cssRules = sheet.cssRules; } catch (e) { continue; }  // cross-origin
    const node = sheet.ownerNode;
    const inlined = node && node.tagName === 'STYLE'
      && node.textContent.trim().length > 0;
    if (inlined) continue;            // already in the markup, leave it there
    for (const rule of cssRules) rules.push(rule.cssText);
  }
  document.querySelectorAll('[style-hover]').forEach(
    el => el.removeAttribute('style-hover'));

  // 2. Links, matched on visible text.
  let removed = 0, wired = 0, relabelled = 0, chips = 0;
  document.querySelectorAll('a').forEach(a => {
    const t = a.textContent.trim();
    if (config.remove.includes(t)) { a.remove(); removed++; return; }
    if (config.links[t]) { a.setAttribute('href', config.links[t]); wired++; }
    if (config.relabel[t]) { a.textContent = config.relabel[t]; relabelled++; }
    // Any remaining href="#" is an example-question chip.
    if (a.getAttribute('href') === '#') {
      a.setAttribute('href', config.demoBot); chips++;
    }
  });

  // 3. Mark the language buttons so the replacement script can find them.
  document.querySelectorAll('button').forEach(b => {
    const t = b.textContent.trim();
    if (['UZ', 'RU', 'EN'].includes(t)) b.setAttribute('data-lang', t);
    b.removeAttribute('onClick');
  });

  // 4. Self-hosted Manrope replaces the fonts.googleapis.com link. That removes
  //    a third party that would otherwise see every visitor's IP before they
  //    have consented to anything -- one fewer clause in the privacy policy.
  document.querySelectorAll('link[href*="fonts.googleapis.com"],'
    + 'link[href*="fonts.gstatic.com"]').forEach(l => l.remove());
  const font = document.createElement('link');
  font.rel = 'stylesheet';
  font.href = 'fonts/manrope.css';
  document.head.appendChild(font);

  // 5. The runtime has done its work; the output must not carry it.
  document.querySelectorAll('script').forEach(s => s.remove());

  const style = document.createElement('style');
  style.textContent = rules.join('\\n');
  document.head.appendChild(style);

  const js = document.createElement('script');
  js.textContent = config.langJs;
  document.body.appendChild(js);

  return {hoverRules: rules.length, wired, removed, relabelled, chips};
}
"""


def verify_bot() -> None:
    """Refuse to render if the bot username is not the one we think it is.

    A render-time network call, deliberately: the alternative is trusting a
    string that has already been wrong once, in a way that produced a working
    link to an unrelated bot rather than a visible failure.
    """
    import urllib.request
    url = f"https://t.me/{BOT_USERNAME}"
    with urllib.request.urlopen(url, timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
    if BOT_TITLE not in body:
        raise SystemExit(
            f"\n{url} does not look like the expected bot: {BOT_TITLE!r} is "
            "not on the page. Refusing to render links that may point at "
            "somebody else's bot.")
    print(f"  verified {url} is {BOT_TITLE!r}")


def main() -> None:
    verify_bot()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(SOURCE.as_uri())
        # The runtime renders on load. Wait for a loop to have produced real
        # nodes rather than for a timer -- the pricing cards come from <sc-for>.
        page.wait_for_function(
            "() => !document.body.innerHTML.includes('{{')", timeout=30000)
        stats = page.evaluate(TRANSFORM, {
            "links": LINKS, "remove": sorted(REMOVE_TEXT), "langJs": LANG_JS,
            "relabel": RELABEL, "demoBot": DEMO_BOT})
        html = page.content()
        browser.close()

    OUT.write_text(html, encoding="utf-8")
    print(f"  {SOURCE.name} -> {OUT}  ({len(html) / 1024:.0f} KB)")
    print(f"  {stats['hoverRules']} hover rules, {stats['wired']} links wired, "
          f"{stats['relabelled']} relabelled, {stats['chips']} chips, "
          f"{stats['removed']} removed")

    # --- the placeholders must have survived --------------------------------
    # Not a formality. The instruction was that nothing unfinished may be
    # completed or dropped, and a render pipeline is exactly the sort of thing
    # that would quietly do either. These assert the page still says it is
    # unfinished where it is.
    must_survive = {
        "the checkerboard QR": "repeating-conic-gradient",
        "screenshot 2 placeholder": "SCREENSHOT 2",
        "screenshot 3 placeholder": "SCREENSHOT 3",
        "screenshot 4 placeholder": "SCREENSHOT 4",
    }
    failures = [name for name, needle in must_survive.items()
                if needle not in html]

    # The four exchanges are data in the runtime block; after rendering they
    # must be real markup. Counted rather than eyeballed.
    bubbles = len(re.findall(r"border-radius: 14px 14px 14px 4px", html))
    print(f"  chat bubbles rendered into markup: {bubbles}")
    if bubbles < 4:
        raise SystemExit("\nthe Telegram exchanges did not render")

    for name in must_survive:
        print(f"  {'ok  ' if name not in failures else 'MISSING'}  {name}")
    if failures:
        raise SystemExit(f"\nplaceholders lost: {failures}. Not writing a page "
                         "that claims to be finished when it is not.")
    if "{{" in html or "<sc-for" in html:
        raise SystemExit("\nunrendered bindings left in the output")
    if 'href="#"' in html:
        raise SystemExit("\ndead href='#' links left in the output")
    if "@talkwisp_bot" in html:
        raise SystemExit("\nthe platform bot is still named in the page")
    if "support.js" in html:
        raise SystemExit("\nthe design-tool runtime is still referenced")
    print("\n  placeholders intact, runtime gone, bindings resolved")


if __name__ == "__main__":
    main()
