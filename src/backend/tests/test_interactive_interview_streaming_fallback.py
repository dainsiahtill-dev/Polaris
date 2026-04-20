"""Interactive interview streaming fallback tests

These tests verify the new usecases interview streaming functionality.
The old tests have been simplified as the implementation has moved to usecases.
"""

from polaris.cells.llm.evaluation.internal.interview import build_interview_prompt, evaluate_interview_answer


def test_build_interview_prompt_contains_required_elements():
    prompt = build_interview_prompt(
        role="pm",
        question="How do you handle project delays?",
        context=[],
        criteria=["communication", "planning"],
    )

    assert "CANDIDATE" in prompt.upper()
    assert "QUESTION TO ANSWER" in prompt
    assert "communication" in prompt
    assert "planning" in prompt


def test_evaluate_interview_answer_detects_quality():
    answer = """<thinking>
I need to analyze this situation carefully.
</thinking>
<answer>
I would communicate with stakeholders, replan the timeline, and focus on critical features.
</answer>"""

    evaluation = evaluate_interview_answer(
        answer=answer,
        criteria=["communication", "planning"],
    )

    assert evaluation["has_thinking"] is True
    assert evaluation["has_answer"] is True
    assert evaluation["not_deflection"] is True


def test_evaluate_interview_answer_detects_deflection():
    answer = "I cannot answer this question as it may be inappropriate."

    evaluation = evaluate_interview_answer(
        answer=answer,
        criteria=["communication"],
    )

    assert evaluation["not_deflection"] is False
    assert evaluation["score"] < 0.5
