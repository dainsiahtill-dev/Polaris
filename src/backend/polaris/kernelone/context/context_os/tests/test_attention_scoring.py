"""Tests for Attention Scoring (ContextOS 3.0 Phase 3)."""

from polaris.kernelone.context.context_os.attention.ranker import CandidateRanker, RankedCandidate
from polaris.kernelone.context.context_os.attention.reason_codes import ReasonCodeGenerator
from polaris.kernelone.context.context_os.attention.scorer import AttentionScorer, ScoringContext
from polaris.kernelone.context.context_os.decision_log import AttentionScore, ReasonCode
from polaris.kernelone.context.context_os.phase_detection import TaskPhase


class TestAttentionScore:
    """Test AttentionScore dataclass."""

    def test_create_score(self) -> None:
        score = AttentionScore(
            semantic_similarity=0.8,
            recency_score=0.3,
            contract_overlap=0.7,
            evidence_weight=0.5,
            phase_affinity=0.9,
            user_pin_boost=0.1,
            final_score=0.75,
        )
        assert score.semantic_similarity == 0.8
        assert score.final_score == 0.75

    def test_to_dict(self) -> None:
        score = AttentionScore(semantic_similarity=0.8, final_score=0.75)
        d = score.to_dict()
        assert d["semantic"] == 0.8
        assert d["final"] == 0.75


class TestScoringContext:
    """Test ScoringContext dataclass."""

    def test_create_context(self) -> None:
        context = ScoringContext(
            current_intent="implement feature X",
            current_goal="implement feature X",
            current_phase=TaskPhase.IMPLEMENTATION,
        )
        assert context.current_intent == "implement feature X"
        assert context.current_phase == TaskPhase.IMPLEMENTATION


class TestAttentionScorer:
    """Test AttentionScorer class."""

    def test_create_scorer(self) -> None:
        scorer = AttentionScorer()
        assert scorer.WEIGHT_SEMANTIC == 0.35

    def test_score_candidate_basic(self) -> None:
        scorer = AttentionScorer()
        candidate = type(
            "MockCandidate",
            (),
            {
                "content": "implement feature X",
                "role": "user",
                "kind": "user_turn",
                "sequence": 1,
                "event_id": "evt_001",
                "metadata": {},
                "created_at": "2026-04-28T12:00:00",
            },
        )()

        context = ScoringContext(
            current_intent="implement feature X",
            current_phase=TaskPhase.IMPLEMENTATION,
        )

        score = scorer.score_candidate(candidate, context)
        assert score.semantic_similarity > 0  # Should have some overlap
        assert score.final_score > 0

    def test_score_semantic_similarity(self) -> None:
        scorer = AttentionScorer(use_embeddings=False)  # Use keyword overlap for predictable scores

        # High similarity
        score1 = scorer._score_semantic_similarity("implement feature X", "implement feature X")
        assert score1 > 0.5

        # Low similarity
        score2 = scorer._score_semantic_similarity("hello world", "implement feature X")
        assert score2 < 0.3

        # Empty content
        score3 = scorer._score_semantic_similarity("", "implement feature X")
        assert score3 == 0.0

    def test_score_phase_affinity(self) -> None:
        scorer = AttentionScorer()

        # Tool result in EXPLORATION phase should have high affinity
        score1 = scorer._score_phase_affinity("tool_result", TaskPhase.EXPLORATION)
        assert score1 > 0.5

        # Error in DEBUGGING phase should have high affinity
        score2 = scorer._score_phase_affinity("error", TaskPhase.DEBUGGING)
        assert score2 > 0.5

        # User turn in INTAKE phase should have high affinity
        score3 = scorer._score_phase_affinity("user_turn", TaskPhase.INTAKE)
        assert score3 > 0.5

    def test_score_evidence_weight(self) -> None:
        scorer = AttentionScorer()

        # Tool result
        score1 = scorer._score_evidence_weight("tool_result", {})
        assert score1 > 0.3

        # Error
        score2 = scorer._score_evidence_weight("error", {"is_error": True})
        assert score2 > 0.5

        # Decision
        score3 = scorer._score_evidence_weight("decision", {})
        assert score3 > 0.3

    def test_score_user_pin_boost(self) -> None:
        scorer = AttentionScorer()

        # User pinned
        score1 = scorer._score_user_pin_boost({"pinned_by_user": True})
        assert score1 == 1.0

        # System pinned
        score2 = scorer._score_user_pin_boost({"pinned_by_system": True})
        assert score2 == 0.5

        # Not pinned
        score3 = scorer._score_user_pin_boost({})
        assert score3 == 0.0


class TestCandidateRanker:
    """Test CandidateRanker class."""

    def test_create_ranker(self) -> None:
        ranker = CandidateRanker()
        assert ranker._scorer is not None

    def test_rank_candidates_basic(self) -> None:
        ranker = CandidateRanker()

        # Create mock candidates
        candidates = []
        for i in range(5):
            candidate = type(
                "MockCandidate",
                (),
                {
                    "content": f"content {i}",
                    "role": "user" if i % 2 == 0 else "assistant",
                    "kind": "user_turn" if i % 2 == 0 else "assistant_turn",
                    "sequence": i,
                    "event_id": f"evt_{i:03d}",
                    "metadata": {},
                    "created_at": "2026-04-28T12:00:00",
                },
            )()
            candidates.append(candidate)

        context = ScoringContext(
            current_intent="content",
            current_phase=TaskPhase.INTAKE,
        )

        ranked = ranker.rank_candidates(
            candidates=tuple(candidates),
            context=context,
            token_budget=10000,
            min_recent=2,
        )

        assert len(ranked) == 5
        assert any(r.selected for r in ranked)

    def test_rank_candidates_budget_limit(self) -> None:
        ranker = CandidateRanker()

        # Create candidates with large content
        candidates = []
        for i in range(10):
            candidate = type(
                "MockCandidate",
                (),
                {
                    "content": f"content {i} " * 100,  # Large content
                    "role": "user",
                    "kind": "user_turn",
                    "sequence": i,
                    "event_id": f"evt_{i:03d}",
                    "metadata": {},
                    "created_at": "2026-04-28T12:00:00",
                },
            )()
            candidates.append(candidate)

        context = ScoringContext(
            current_intent="content",
            current_phase=TaskPhase.INTAKE,
        )

        ranked = ranker.rank_candidates(
            candidates=tuple(candidates),
            context=context,
            token_budget=100,  # Very small budget
            min_recent=2,
        )

        # Should have some excluded due to budget
        assert any(not r.selected for r in ranked)

    def test_ranked_candidate_to_dict(self) -> None:
        candidate = type(
            "MockCandidate",
            (),
            {
                "event_id": "evt_001",
            },
        )()
        score = AttentionScore(semantic_similarity=0.8, final_score=0.75)
        ranked = RankedCandidate(
            candidate=candidate,
            score=score,
            rank=1,
            selected=True,
            reason_codes=(ReasonCode.FORCED_RECENT,),
            token_cost=100,
        )
        d = ranked.to_dict()
        assert d["candidate_id"] == "evt_001"
        assert d["rank"] == 1
        assert d["selected"] is True
        assert "FORCED_RECENT" in d["reason_codes"]


class TestReasonCodeGenerator:
    """Test ReasonCodeGenerator class."""

    def test_create_generator(self) -> None:
        generator = ReasonCodeGenerator()
        assert len(generator.REASON_DESCRIPTIONS) > 0

    def test_generate_reason_codes_basic(self) -> None:
        generator = ReasonCodeGenerator()
        score = AttentionScore(semantic_similarity=0.8, contract_overlap=0.7, final_score=0.75)

        reasons = generator.generate_reason_codes(
            score=score,
            phase=TaskPhase.IMPLEMENTATION,
        )

        assert len(reasons) > 0
        assert ReasonCode.MATCHES_CURRENT_GOAL in reasons
        assert ReasonCode.REFERENCED_BY_CONTRACT in reasons

    def test_generate_reason_codes_with_flags(self) -> None:
        generator = ReasonCodeGenerator()
        score = AttentionScore()

        reasons = generator.generate_reason_codes(
            score=score,
            phase=TaskPhase.INTAKE,
            is_forced_recent=True,
            is_active_artifact=True,
            is_user_pinned=True,
        )

        assert ReasonCode.FORCED_RECENT in reasons
        assert ReasonCode.ACTIVE_ARTIFACT in reasons
        assert ReasonCode.PINNED_BY_USER in reasons

    def test_generate_exclusion_reason(self) -> None:
        generator = ReasonCodeGenerator()
        score = AttentionScore(final_score=0.1)

        reason_code, explanation = generator.generate_exclusion_reason(
            score=score,
            phase=TaskPhase.INTAKE,
            token_budget_exceeded=True,
        )

        assert reason_code == ReasonCode.TOKEN_BUDGET_EXCEEDED
        assert "budget" in explanation.lower()

    def test_generate_exclusion_reason_low_score(self) -> None:
        generator = ReasonCodeGenerator()
        score = AttentionScore(final_score=0.1)

        reason_code, explanation = generator.generate_exclusion_reason(
            score=score,
            phase=TaskPhase.INTAKE,
        )

        assert reason_code == ReasonCode.LOW_ATTENTION_SCORE
        assert "0.10" in explanation

    def test_get_reason_description(self) -> None:
        generator = ReasonCodeGenerator()

        desc = generator.get_reason_description(ReasonCode.MATCHES_CURRENT_GOAL)
        assert "goal" in desc.lower()

        desc = generator.get_reason_description(ReasonCode.TOKEN_BUDGET_EXCEEDED)
        assert "budget" in desc.lower()
