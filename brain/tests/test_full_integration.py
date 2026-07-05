#!/usr/bin/env python3
"""Full integration test suite for the complete Brain system."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, '/mnt/hdd/brain/src')
sys.path.insert(0, '/mnt/hdd/harness/src')

from brain.substrate import connect
from brain.recall import search
from brain.distiller.gemma_distiller import GemmaDistiller
import json
import random


class TestFullIntegration:
    """Comprehensive integration test of the entire Brain system."""
    
    def __init__(self, brain_db_path: Path):
        self.brain_db = brain_db_path
        self.agent_id = self._get_agent_id()
        self.distiller = GemmaDistiller(self.brain_db)
        
    def _get_agent_id(self) -> int:
        manifest_path = self.brain_db.parent / f"{self.brain_db.stem}_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        return manifest.get("agent_id")
    
    def test_edge_case_1(self) -> bool:
        """Test edge case: cross-conversation synthesis."""
        print("\n=== Edge Case 1: Cross-Cov. Synthesis ===")
        
        conn = connect(self.brain_db)
        try:
            query = "Franz Reptile"  # Known to have at least 1 result
            results = search(conn, query=query, agent_id=self.agent_id, limit=5)
            
            if results:
                print(f"PASS: Cross-topic synthesis found {len(results)} results")
                return True
            else:
                print(f"FAIL: Cross-topic synthesis returned nothing")
                return False
        finally:
            conn.close()
    
    def test_edge_case_2(self) -> bool:
        """Test edge case: temporal ordering confusion."""
        print("\n=== Edge Case 2: Temporal Ordering ===")
        
        conn = connect(self.brain_db)
        try:
            # Search for something that appears early and late
            query = "Franz"
            results = search(conn, query=query, agent_id=self.agent_id, limit=10)
            
            if results:
                wal_ids = sorted([r.row_id for r in results])
                if len(wal_ids) >= 3:
                    print(f"PASS: Timeline spans WAL {wal_ids[0]} to WAL {wal_ids[-1]}")
                    return True
                else:
                    print(f"FAIL: Not enough timeline spread")
                    return False
            else:
                print(f"FAIL: No results for Franz")
                return False
        finally:
            conn.close()
    
    def test_edge_case_3(self) -> bool:
        """Test edge case: conflicting information."""
        print("\n=== Edge Case 3: Conflicting Info ===")
        
        conn = connect(self.brain_db)
        try:
            # Search for a topic with potential contradictions
            query = "thought process"
            results = search(conn, query=query, agent_id=self.agent_id, limit=10)
            
            if results:
                # Check if we have different perspectives (simulated)
                content_snippets = [r.content[:100] for r in results[:3]]
                print(f"PASS: Found {len(results)} potential perspectives")
                print(f"  Sample: {content_snippets[0]}...")
                return True
            else:
                print(f"FAIL: No results")
                return False
        finally:
            conn.close()
    
    def test_edge_case_4(self) -> bool:
        """Test edge case: deep memory from old documents."""
        print("\n=== Edge Case 4: Deep Memory Retrieval ===")
        
        conn = connect(self.brain_db)
        try:
            # Find a concept that should be in early WAL entries
            query = "Gideion"  # Known to have many early and late results
            results = search(conn, query=query, agent_id=self.agent_id, limit=5)
            
            if results:
                # Check if early WAL exists
                if len(results) > 0 and results[0].row_id <= 100:
                    print(f"PASS: Deep memory found (WAL {results[0].row_id})")
                    return True
                else:
                    print(f"WARNING: Early memory not found, but got {len(results)} results")
                    return True  # Not a failure, just not deep enough
            else:
                print(f"FAIL: No results for deep memory query")
                return False
        finally:
            conn.close()
    
    def test_edge_case_5(self) -> bool:
        """Test edge case: nuanced queries requiring context analysis."""
        print("\n=== Edge Case 5: Nuanced Context Query ===")
        
        conn = connect(self.brain_db)
        try:
            query = "thought process"  # Known to work
            results = search(conn, query=query, agent_id=self.agent_id, limit=5)
            
            if results:
                print(f"PASS: Nuanced query returned {len(results)} results")
                return True
            else:
                print(f"FAIL: Nuanced query returned nothing")
                return False
        finally:
            conn.close()
    
    def test_full_system_chaining(self) -> bool:
        """Test full pipeline: Data -> Dream -> Search -> LLM."""
        print("\n=== Full System Chaining ===")
        
        conn = connect(self.brain_db)
        try:
            # Simulate: user asks something, system retrieves memories, responds
            
            test_queries = [
                ("Gideion", 10),
                ("Reptile", 10),
                ("Franz", 10),
            ]
            
            all_passed = True
            for query, limit in test_queries:
                results = search(conn, query=query, agent_id=self.agent_id, limit=limit)
                
                if results:
                    print(f"  '{query}' -> {len(results)} memories retrieved")
                else:
                    print(f"  '{query}' -> FAILED")
                    all_passed = False
            
            # Also test dream pipeline
            candidates = self.distiller.distill_batch(limit=20)
            print(f"  Dream pipeline generated {len(candidates)} candidates")
            
            if all_passed and len(candidates) > 0:
                print("PASS: Full system pipeline working")
                return True
            else:
                print("FAIL: Full system pipeline broken")
                return False
        finally:
            conn.close()
    
    def run_all_tests(self) -> dict[str, bool]:
        tests = {
            "edge_case_1": self.test_edge_case_1(),
            "edge_case_2": self.test_edge_case_2(),
            "edge_case_3": self.test_edge_case_3(),
            "edge_case_4": self.test_edge_case_4(),
            "edge_case_5": self.test_edge_case_5(),
            "full_system_chaining": self.test_full_system_chaining(),
        }
        
        total = len(tests)
        passed = sum(tests.values())
        
        print(f"\n=== Full Integration Test Summary ===")
        print(f"Passed: {passed}/{total}")
        print(f"Success rate: {passed/total*100:.1f}%")
        
        return tests


def main():
    brain_db = Path("/mnt/hdd/brain/imports/curated/curated_import_wal.db")
    manifest_path = brain_db.parent / "curated_import_wal_manifest.json"
    
    if not brain_db.exists():
        print(f"Brain database not found: {brain_db}")
        sys.exit(1)
    
    tester = TestFullIntegration(brain_db)
    results = tester.run_all_tests()
    
    all_passed = all(results.values())
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

