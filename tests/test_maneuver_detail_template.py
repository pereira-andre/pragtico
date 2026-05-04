from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _maneuver_detail_template() -> str:
    return (ROOT / "templates" / "maneuver_detail.html").read_text(encoding="utf-8")


def test_maneuver_action_forms_are_collapsed_disclosures() -> None:
    template = _maneuver_detail_template()

    assert 'data-action-accordion' in template
    assert template.count('data-action-disclosure') >= 4
    assert 'class="maneuver-action-summary"' in template

    for action in (
        "Editar marcação",
        "Aprovar {{ maneuver.title|lower }}",
        "Abortar {{ maneuver.title|lower }}",
        "Registar manobra",
    ):
        assert action in template

    assert 'class="stack planning-optional-body maneuver-action-body"' in template
    assert 'class="ops-form-card maneuver-inline-form"' not in template


def test_maneuver_action_accordion_closes_other_open_forms() -> None:
    template = _maneuver_detail_template()

    assert 'querySelectorAll("[data-action-accordion]")' in template
    assert 'candidate !== details' in template
    assert 'candidate.open = false' in template
