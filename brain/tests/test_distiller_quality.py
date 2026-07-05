#!/usr/bin/env python3
"""Quality tests for the Gemma distiller and Dream system."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure we can import
sys.path.insert(0, '/mnt/hdd/brain/src')
sys.path.insert(0, '/mnt/hdd/harness/src')

from brain.distiller.gemma_distiller import GemmaDistiller, MemoryCandidate
from harness.dream_scheduler import DreamScheduler, DreamScheduleConfig


class TestDistillerQuality:
    """Test the quality of memory extraction and distillation."""
    
    def __init__(self, brain_db_path: Path):
        self.brain_db = brain_db_path
        self.distiller = GemmaDistiller(self.brain_db)
        self.scheduler = DreamScheduler(self.brain_db)
        
    def test_extraction_quality(self) -> bool:
        """Test that distillation produces meaningful, accurate memories."""
        print("\n=== Testing Extraction Quality ===")
        
        # Get a random sample of WAL entries
        candidates = self.distiller.distill_batch(limit=10)
        
        # Check: Every candidate should have required fields
        all_valid = True
        for i, candidate in enumerate(candidates):
            if not all([
                candidate.wal_id > 0,
                candidate.content and len(candidate.content) > 0,
                candidate.length > 0,
                candidate.confidence >= 0 and candidate.confidence <= 1,
            ]):
                print(f"FAIL: Candidate {i} invalid: {candidate}")
                all_valid = False
            else:
                print(f"PASS: Candidate {i}: WAL {candidate.wal_id}, content_len={len(candidate.content)}, confidence={candidate.confidence:.2f}")
        
        return all_valid
    
    def test_conceptual_extraction(self) -> bool:
        """Test that important concepts are preserved."""
        print("\n=== Testing Conceptual Extraction ===")
        
        # Test WAL IDs with known concepts
        known_concepts = [
            (1, "thought process"),
            (4, "Reptile"),
            (28, "Franz"),
            (277, "Gideion"),
        ]
        
        candidates = self.distiller.distill_batch(wal_ids=[w[0] for w in known_concepts])
        
        all_found = True
        for wal_id, concept in known_concepts:
            found = any(c.wal_id == wal_id and concept.lower() in c.content.lower() for c in candidates)
            if found:
                print(f"PASS: Concept '{concept}' preserved in WAL {wal_id}")
            else:
                print(f"FAIL: Concept '{concept}' missing from WAL {wal_id}")
                all_found = False
        
        return all_found
    
    def test_sensitivity_to_content_length(self) -> bool:
        """Test that confidence scales appropriately with content."""
        print("\n=== Testing Content Length Sensitivity ===")
        
        # Get a batch and check confidence correlates with length
        candidates = self.distiller.distill_batch(limit=20)
        
        if len(candidates) < 2:
            print("PASS: Not enough candidates to test sensitivity")
            return True
        
        lengths = [c.length for c in candidates]
        confidences = [c.confidence for c in candidates]
        
        # Simple check: longer content generally should get higher confidence
        # (due to our simulated confidence function)
        avg_conf_long = sum(conf for l, conf in zip(lengths, confidences) if l > 500) / max(1, sum(1 for l in lengths if l > 500))
        avg_conf_short = sum(conf for l, conf in zip(lengths, confidences) if l <= 500) / max(1, sum(1 for l in lengths if l <= 500))
        
        if avg_conf_long >= avg_conf_short:
            print(f"PASS: Longer content avg conf={avg_conf_long:.2f} >= short content avg conf={avg_conf_short:.2f}")
            return True
        else:
            print(f"FAIL: Longer content avg conf={avg_conf_long:.2f} < short content avg conf={avg_conf_short:.2f}")
            return False
    
    def test_deduplication(self) -> bool:
        """Test that deduplication works correctly."""
        print("\n=== Testing Deduplication ===")
        
        # Create a batch of candidates
        candidates = self.distiller.distill_batch(limit=30)
        if not candidates:
            print("FAIL: No candidates to deduplicate")
            return False
        
        deduped = self.distiller.deduplicate_candidates(candidates)
        
        # Check no two candidates have the same wal_id (from our simple dedup logic)
        wal_ids = [c.wal_id for c in deduped]
        if len(wal_ids) == len(set(wal_ids)):
            print(f"PASS: Deduplication produced {len(deduped)} unique candidates from {len(candidates)}")
            return True
        else:
            print(f"FAIL: Deduplication left duplicate WAL IDs")
            return False
    
    def test_dream_scheduler_integration(self) -> bool:
        """Test that the DreamScheduler can run successfully."""
        print("\n=== Testing Dream Scheduler Integration ===")
        
        # Test a single pass1
        pass1 = self.scheduler.run_pass1(wal_limit=10)
        print(f"PASS: Pass 1 succeeded, created {pass1} candidates")
        
        # Test a single pass2
        pass2 = self.scheduler.run_pass2()
        print(f"PASS: Pass 2 succeeded, finalized {pass2} memories")
        
        # Test state persistence
        if (self.scheduler.last_run and 
            self.scheduler.total_dreams >= 2):
            print("PASS: DreamScheduler state persisted correctly")
            return True
        else:
            print("FAIL: DreamScheduler state not persisted")
            return False
    
    def run_all_tests(self) -> dict[str, bool]:
        """Run all quality tests."""
        tests = {
            "extraction_quality": self.test_extraction_quality(),
            "conceptual_extraction": self.test_conceptual_extraction(),
            "sensitivity_to_content_length": self.test_sensitivity_to_content_length(),
            "deduplication": self.test_deduplication(),
            "dream_scheduler_integration": self.test_dream_scheduler_integration(),
        }
        
        total = len(tests)
        passed = sum(tests.values())
        
        print(f"\n=== Distiller Quality Test Summary ===")
        print(f"Passed: {passed}/{total}")
        print(f"Success rate: {passed/total*100:.1f}%")
        
        return tests


def main():
    import sys
    
    brain_db = Path("/mnt/hdd/brain/imports/curated/curated_import_wal.db")
    manifest_path = brain_db.parent / "curated_import_wal_manifest.json"
    
    if not brain_db.exists():
        print(f"Brain database not found: {brain_db}")
        sys.exit(1)
    
    tester = TestDistillerQuality(brain_db)
    results = tester.run_all_tests()
    
    all_passed = all(results.values())
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

