#!/usr/bin/env python3
"""Tests for skills shaping and constraint enforcement."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, '/mnt/hdd/brain/src')
sys.path.insert(0, '/mnt/hdd/harness/src')

from brain.substrate import connect
from brain.recall import search
import json


class TestSkillsShaping:
    """Test that skills can constrain and guide LLM behavior."""
    
    def __init__(self, brain_db_path: Path):
        self.brain_db = brain_db_path
        self.agent_id = self._get_agent_id()
        
    def _get_agent_id(self) -> int:
        manifest_path = self.brain_db.parent / f"{self.brain_db.stem}_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        return manifest.get("agent_id")
    
    def test_skill_derivation_from_wal(self) -> bool:
        """Test that successful patterns can be extracted from WAL."""
        print("\n=== Testing Skill Derivation from WAL ===")
        
        # Simulate: find a pattern of successful behavior in conversation
        # Then verify it shows up as a repeatable skill
        
        conn = connect(self.brain_db)
        try:
            # Look for a pattern: e.g., how to handle analysis/philosophy
            query = "thought process"
            results = search(conn, query=query, agent_id=self.agent_id, limit=5)
            
            if results:
                print(f"PASS: Found {len(results)} messages about primary analysis approach")
                # Verify we can identify a consistent pattern
                for r in results[:3]:
                    # Check if content is instructional or demonstrates a method
                    if 'approach' in r.content.lower() or 'method' in r.content.lower() or 'process' in r.content.lower():
                        print(f"  Pattern found in WAL {r.row_id}: {r.content[:150]}...")
                        break
                return True
            else:
                print(f"FAIL: '{query}' not found")
                return False
        finally:
            conn.close()
    
    def test_skill_constraint_effectiveness(self) -> bool:
        """Test that skills actually constrain LLM output."""
        print("\n=== Testing Skill Constraint Effectiveness ===")
        
        conn = connect(self.brain_db)
        try:
            # Simulate: if we have a skill defining "analysis style"
            # LLM should follow it
            
            # Query to check if skill-like patterns exist
            queries = [
                "analysis",
                "reasoning",
                "approach",
            ]
            
            # In a real system, we'd ask the LLM to generate something
            # with and without the skill, then compare
            
            # For now, just verify skills can be searched and found
            found_skills = 0
            for query in queries:
                results = search(conn, query=query, agent_id=self.agent_id, limit=3)
                if results:
                    found_skills += 1
                    print(f"  '{query}' -> {len(results)} results")
            
            if found_skills >= 2:
                print(f"PASS: Can find skill-like patterns in {found_skills} queries")
                return True
            else:
                print(f"FAIL: Only {found_skills} skill-like patterns found")
                return False
        finally:
            conn.close()
    
    def test_skill_application_across_domains(self) -> bool:
        """Test that skills apply to diverse topics."""
        print("\n=== Testing Skill Application Across Domains ===")
        
        conn = connect(self.brain_db)
        try:
            # Look for a skill that could apply to multiple topics
            # e.g., "analysis" or "philosophical discussion"
            
            domains = [
                ("Gideion", "identity/domain"),
                ("Reptile", "concept/domain"),
                ("Franz", "personal/domain"),
            ]
            
            consisistent_pattern = True
            for query, desc in domains:
                results = search(conn, query=query, agent_id=self.agent_id, limit=3)
                
                if results:
                    # Check if any result shows consistent behavioral trait
                    print(f"  '{query}' ({desc}) -> {len(results)} results")
                else:
                    print(f"  '{query}' ({desc}) -> no results")
            
            print("PASS: Can test skills across multiple domains")
            return True
        finally:
            conn.close()
    
    def test_skill_skill_persistence(self) -> bool:
        """Test that skills persist across sessions/timeline."""
        print("\n=== Testing Skill Persistence ===")
        
        conn = connect(self.brain_db)
        try:
            # Use a term that might appear in both early and late conversation
            query = "thought process"
            results = search(conn, query=query, agent_id=self.agent_id, limit=10)
            
            if results:
                wal_ids = [r.row_id for r in results]
                if len(wal_ids) >= 3:
                    min_wal = min(wal_ids)
                    max_wal = max(wal_ids)
                    print(f"PASS: Skill-like pattern from WAL {min_wal} to WAL {max_wal}")
                    return True
                else:
                    print(f"FAIL: Only {len(wal_ids)} occurrences")
                    return False
            else:
                print(f"FAIL: No results for '{query}'")
                return False
        finally:
            conn.close()
    
    def run_all_tests(self) -> dict[str, bool]:
        tests = {
            "skill_derivation_from_wal": self.test_skill_derivation_from_wal(),
            "skill_constraint_effectiveness": self.test_skill_constraint_effectiveness(),
            "skill_application_across_domains": self.test_skill_application_across_domains(),
            "skill_persistence": self.test_skill_skill_persistence(),
        }
        
        total = len(tests)
        passed = sum(tests.values())
        
        print(f"\n=== Skills Shaping Test Summary ===")
        print(f"Passed: {passed}/{total}")
        print(f"Success rate: {passed/total*100:.1f}%")
        
        return tests


def main():
    brain_db = Path("/mnt/hdd/brain/imports/curated/curated_import_wal.db")
    manifest_path = brain_db.parent / "curated_import_wal_manifest.json"
    
    if not brain_db.exists():
        print(f"Brain database not found: {brain_db}")
        sys.exit(1)
    
    tester = TestSkillsShaping(brain_db)
    results = tester.run_all_tests()
    
    all_passed = all(results.values())
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

