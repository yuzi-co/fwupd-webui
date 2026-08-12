from fwupd_webui.web.app import plain_text


def test_strips_paragraph_tags():
    assert plain_text("<p>Fixes a bug.</p>") == "Fixes a bug."


def test_keeps_paragraphs_separate():
    out = plain_text("<p>First thing.</p><p>Second thing.</p>")
    assert "First thing." in out
    assert "Second thing." in out
    assert "<p>" not in out


def test_renders_list_items_as_bullets():
    out = plain_text("<ul><li>One</li><li>Two</li></ul>")
    assert "One" in out and "Two" in out
    assert "<li>" not in out


def test_unescapes_entities():
    assert plain_text("<p>a &amp; b &lt; c</p>") == "a & b < c"


def test_passes_through_plain_text():
    assert plain_text("Just a sentence.") == "Just a sentence."


def test_handles_none_and_empty():
    assert plain_text(None) == ""
    assert plain_text("") == ""


def test_does_not_reintroduce_markup_for_the_template_to_trust():
    """The output is escaped again by Jinja, so a description containing a
    script tag must survive as visible text, never as markup."""
    out = plain_text("<p>hi</p><script>alert(1)</script>")
    assert "<script>" not in out
    assert "alert(1)" in out
