def test_card_suite_cases_are_valid():
    from arcana.evals.suites.cards import CARD_CASES

    assert len(CARD_CASES) > 0
    for case in CARD_CASES:
        assert case.suite == "cards"
        assert case.id.startswith("cards-")
        assert case.prompt
        assert case.rubric


def test_blending_suite_cases_have_modifiers():
    from arcana.evals.suites.blending import BLENDING_CASES

    assert len(BLENDING_CASES) > 0
    for case in BLENDING_CASES:
        assert case.suite == "blending"
        assert len(case.modifier_cards) > 0


def test_all_case_ids_are_unique():
    from arcana.evals.suites.blending import BLENDING_CASES
    from arcana.evals.suites.cards import CARD_CASES

    all_cases = [*CARD_CASES, *BLENDING_CASES]
    ids = [c.id for c in all_cases]
    assert len(ids) == len(set(ids)), "Duplicate eval case IDs found"
