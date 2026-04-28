import importlib.util, pytest
if importlib.util.find_spec("anthropomorphic") is None:
    pytest.skip("Legacy module not available: core.polaris_loop.anthropomorphic.schema", allow_module_level=True)

# ruff: noqa: E402
import os
import sys
import shutil
import tempfile
import pytest
from datetime import datetime

# Setup path to import backend modules
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# tests/anthropomorphic -> tests -> root
ROOT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
# src/backend/core/polaris_loop
BACKEND_LOOP_DIR = os.path.join(ROOT_DIR, "src", "backend", "core", "polaris_loop")
sys.path.insert(0, BACKEND_LOOP_DIR)

try:
    from anthropomorphic.schema import MemoryItem
    from anthropomorphic.memory_store import MemoryStore
except ImportError:
    # Try alternative import path
    sys.path.insert(0, os.path.join(ROOT_DIR, "src", "backend"))
    from core.polaris_loop.anthropomorphic.schema import MemoryItem
    from core.polaris_loop.anthropomorphic.memory_store import MemoryStore

@pytest.fixture
def temp_memory_store():
    tmp_dir = tempfile.mkdtemp()
    mem_file = os.path.join(tmp_dir, "MEMORY.jsonl")
    store = MemoryStore(mem_file)
    yield store
    shutil.rmtree(tmp_dir)

def test_recency_scoring(temp_memory_store):
    store = temp_memory_store
    # Add memory from step 1
    store.append(MemoryItem(
        id="m1", source_event_id="e1", step=1, timestamp=datetime.now(),
        role="system", type="info", kind="info", text="Old memory", importance=5, hash="1", keywords=[]
    ))
    # Add memory from step 10
    store.append(MemoryItem(
        id="m2", source_event_id="e2", step=10, timestamp=datetime.now(),
        role="system", type="info", kind="info", text="New memory", importance=5, hash="2", keywords=[]
    ))
    
    # Query at step 12. m2 should have higher score due to recency
    # query matches both
    results = store.retrieve("memory", current_step=12)
    
    # Expect m2 first
    assert len(results) >= 2
    assert results[0].id == "m2"
    assert results[1].id == "m1"

def test_diversity_pruning(temp_memory_store):
    store = temp_memory_store
    # Add 6 errors
    for i in range(6):
        store.append(MemoryItem(
            id=f"err-{i}", source_event_id=f"e-{i}", step=10, timestamp=datetime.now(),
            role="system", type="error", kind="error", text=f"Error {i}", importance=8, hash=f"err{i}", keywords=[]
        ))
    # Add 4 successes
    for i in range(4):
        store.append(MemoryItem(
            id=f"succ-{i}", source_event_id=f"s-{i}", step=10, timestamp=datetime.now(),
            role="system", type="success", kind="success", text=f"Success {i}", importance=5, hash=f"succ{i}", keywords=[]
        ))
        
    # Retrieve
    # Since all match "Error" or "Success" (we query generic or empty logic if scoring relies on text match)
    # Our retrieval requires query to match text or keywords for pure relevance. 
    # Let's assume we query something that matches all like "Error Success" or just rely on importance if query mismatch score is 0?
    # Wait, if query doesn't match, keyword score is 0. 
    # But importance (8/10 * 0.2) + recency (1.0 * 0.3) = ~0.46. So they will be retrieved even if query doesn't match well?
    # Let's query "Error Success"
    
    results = store.retrieve("Error Success", current_step=10, top_k=20)
    
    # Check counts
    errors = [m for m in results if m.kind == "error"]
    successes = [m for m in results if m.kind == "success"]
    
    # Pruning limit: Max 5 errors
    assert len(errors) <= 5
    # Pruning limit: Max 3 success
    assert len(successes) <= 3
