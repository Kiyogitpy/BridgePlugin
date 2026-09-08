import pytest
import controller
import skillmulti


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: [])
    monkeypatch.setattr(controller, "get_widget", lambda g, c, index=None: None)


def test_strip_tags_removes_colour_tags():
    assert skillmulti.strip_tags("<col=ff9040>Maple longbow</col>") == "Maple longbow"


def test_menu_label_drops_the_unstrung_suffix():
    assert skillmulti.menu_label("Yew longbow (u)") == "yew longbow"


def test_item_widget_matches_on_name_and_derives_the_option_number(monkeypatch):
    dump = [
        {"group": 270, "child": 15, "name": "", "text": "60 arrow shafts", "screenX": 10},
        {"group": 270, "child": 16, "name": "", "text": "<col=ff9040>Yew shortbow</col>",
         "screenX": 10},
        {"group": 270, "child": 17, "name": "", "text": "<col=ff9040>Yew longbow</col>",
         "screenX": 10},
    ]
    monkeypatch.setattr(controller, "get_widgets_dump", lambda scan=False: dump)
    w = skillmulti.item_widget("Yew longbow (u)")
    assert w["child"] == 17
    assert skillmulti.option_number(w) == 3


def test_prompt_title_distinguishes_the_carve_prompt_from_the_fletch_prompt(monkeypatch):
    monkeypatch.setattr(controller, "get_widget",
                        lambda g, c, index=None: {"text": "What animal would you like to carve?"})
    assert skillmulti.showing(skillmulti.TITLE_CARVE) is True
    assert skillmulti.showing(skillmulti.TITLE_MAKE) is False


def test_prompt_title_is_empty_when_the_widget_is_unavailable():
    assert skillmulti.prompt_title() == ""
    assert skillmulti.showing(skillmulti.TITLE_CARVE) is False
