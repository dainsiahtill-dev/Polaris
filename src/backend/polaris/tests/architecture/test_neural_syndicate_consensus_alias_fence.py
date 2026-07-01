"""Architecture fence for retired Neural Syndicate consensus aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.multi_agent.neural_syndicate as neural_syndicate
import polaris.kernelone.multi_agent.neural_syndicate.consensus as consensus

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONSENSUS_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "multi_agent" / "neural_syndicate" / "consensus.py"
PACKAGE_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "multi_agent" / "neural_syndicate" / "__init__.py"


def test_vote_result_alias_is_retired() -> None:
    """ConsensusResult is the single public result type for voting rounds."""
    assert hasattr(consensus, "ConsensusResult")
    assert hasattr(neural_syndicate, "ConsensusResult")

    assert not hasattr(consensus, "VoteResult")
    assert "VoteResult" not in consensus.__all__
    assert not hasattr(neural_syndicate, "VoteResult")
    assert "VoteResult" not in neural_syndicate.__all__


def test_consensus_sources_do_not_reintroduce_vote_result_alias() -> None:
    """Source-level fence blocks the old VoteResult compatibility export."""
    for path in (CONSENSUS_MODULE, PACKAGE_MODULE):
        source = path.read_text(encoding="utf-8")
        assert "VoteResult = ConsensusResult" not in source
        assert "VoteResult" not in source
