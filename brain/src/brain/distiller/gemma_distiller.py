#!/usr/bin/env python3
"""Gemma distiller for Brain waking and dreaming.

This module provides a model-backed approach to extracting meaningful memories
from WAL data using Gemma Heretic as the distilled intelligence.
"""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field, asdict
import sqlite3
import tempfile

# Try to import brain modules
try:
    from brain.substrate import connect
    from brain.recall import search
except ImportError:
    # Fallback for direct execution
    import sys
    sys.path.insert(0, '/mnt/hdd/brain/src')
    from brain.substrate import connect
    from brain.recall import search


@dataclass
class MemoryCandidate:
    """A single memory candidate extracted from WAL."""
    wal_id: int
    content: str
    length: int
    created_at: str
    sha256: str
    confidence: float = 0.0
    memory_type: str = "provisional"
    sha256: str = ""
    is_duplicate: bool = False
    parent_wal_ids: List[int] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryCandidate":
        return cls(**data)


class GemmaDistiller:
    """
    Uses Gemma Herictic model to identify, extract, and distill meaningful
    memories from WAL data.
    """
    
    def __init__(self, brain_db_path: Path, system_prompt: str = "You are a memory extraction assistant. Identify meaningful, persistent thoughts, facts, and patterns from conversation data."):
        self.brain_db_path = brain_db_path
        self.system_prompt = system_prompt
    
    def distill(self, text: str, wal_id: int, created_at: str, sha256: str = "", batch_id: str = "default", n: int = 1) -> MemoryCandidate:
        """
        Extract a single memory candidate from WAL data.
        
        Args:
            text: WAL content text
            wal_id: WAL row ID
            created_at: Timestamp
            sha256: SHA256 hash of content
            batch_id: Identifier for the batch (unused in this simulation)
            n: Number of memories to extract (unused - always 1)
            
        Returns:
            MemoryCandidate
        """
        # Use a sophisticated extraction prompt rather than empty
        prompt = f"""Analyze this conversation log entry and extract key information that could become a lasting memory or insight.
        
Log content:
"{text[:1000]}"

Extract and summarize the core meaning, capturing:
1. Key facts or claims
2. Personal beliefs or perspectives
3. Important context about identity, relationships, or processes
4. Any innovative or novel thoughts
5. Questions or uncertainties that remain unresolved

Provide ONE concise memory entry (1-2 paragraphs max) that preserves the essential meaning while removing conversational filler. The memory should be something someone could remember and use in future reasoning.

Output ONLY the distilled memory text, nothing else."""
        
        # For a real implementation, would call the gemma model here
        # Simulated response based on content analysis
        extracted = self._simulate_extraction(text, wal_id, created_at, sha256)
        return extracted
    
    def _simulate_extraction(self, text: str, wal_id: int, created_at: str, sha256: str = "") -> MemoryCandidate:
        """
        Simulate memory extraction (placeholder for actual Gemma call).
        In production, would use a real model inference API.
        """
        # Extract meaningful content by analyzing key concepts
        # This is a heuristic extraction
        words = text.split()
        
        # Look for key conceptual markers
        key_concepts = [
            ("Gideion", "identity/meaning"),
            ("Reptile", "persona/concept"),
            ("Franz", "person/context"),
            ("thought process", "cognitive approach"),
            ("business", "work/endeavor"),
            ("start from scratch", "approach/strategy"),
        ]
        
        found_concepts = []
        for keyword, description in key_concepts:
            if keyword.lower() in text.lower():
                found_concepts.append(f"{keyword} ({description})")
        
        # Generate summary based on length and key concepts
        if len(text) > 1000:
            summary = text[:500].strip() + "... (truncated)"
        else:
            summary = text.strip()
        
        # Add detected concepts to summary metadata
        if found_concepts:
            summary += f" [Detected: {', '.join(found_concepts)}]"
        
        # Simulate a confidence score based on content richness
        confidence = self._calculate_confidence(text, found_concepts)
        
        # Extract memory candidate
        return MemoryCandidate(
            wal_id=wal_id,
            content=summary,
            length=len(text),
            created_at=created_at,
            sha256=sha256,
            confidence=confidence,
            memory_type="provisional"
        )
    
    def _calculate_confidence(self, text: str, found_concepts: list) -> float:
        """Calculate confidence score based on content analysis."""
        # Base score on several factors
        score = 0.5  # Base confidence
        
        # Richer content gets higher confidence
        if len(text) > 200:
            score += 0.2
        if len(text) > 500:
            score += 0.1
            
        # Conceptual complexity adds confidence
        score += len(found_concepts) * 0.1
        
        # Cap at 1.0
        return min(1.0, score)
    
    def distill_batch(self, wal_ids: Optional[List[int]] = None, limit: int = 50) -> List[MemoryCandidate]:
        """
        Distill a batch of WAL entries into memory candidates.
        
        Args:
            wal_ids: Specific WAL IDs to process (None for all recent)
            limit: Max number of WAL entries to process
            
        Returns:
            List of MemoryCandidate
        """
        # Connect to brain DB
        conn = connect(self.brain_db_path)
        
        try:
            if wal_ids:
                # Process specific IDs
                placeholders = ",".join("?" for _ in wal_ids)
                query = f"""
                SELECT wal_id, content, created_at 
                FROM wal WHERE wal_id IN ({placeholders})
                """
                cursor = conn.execute(query, wal_ids)
                cursor.row_factory = sqlite3.Row
            else:
                # Process most recent WAL entries
                query = """
                SELECT wal_id, content, created_at 
                FROM wal ORDER BY wal_id DESC LIMIT ?
                """
                cursor = conn.execute(query, (limit,))
            cursor.row_factory = sqlite3.Row
            
            candidates = []
            for row in cursor.fetchall():
                candidate = self.distill(
                    text=row["content"],
                    wal_id=row["wal_id"],
                    created_at=row["created_at"]
                )
                candidates.append(candidate)
            
            return candidates
            
        finally:
            conn.close()
    
    def deduplicate_candidates(self, candidates: List[MemoryCandidate], 
                                threshold: float = 0.8) -> List[MemoryCandidate]:
        """Remove near-duplicate candidate memories."""
        # Very simplified deduplication - would use fuzzy matching in real impl
        unique = []
        for candidate in candidates:
            is_dup = False
            for unique_candidate in unique:
                # Simple heuristic: if same wal_id but different content, keep higher confidence
                if candidate.wal_id == unique_candidate.wal_id:
                    is_dup = candidate.confidence <= unique_candidate.confidence
                    break
            if not is_dup:
                unique.append(candidate)
        return unique


def process_dreamullan_brain(brain_db_path: Path, distiller: GemmaDistiller, 
                              batch_id: str = "default", deduplicate: bool = True) -> int:
    """
    Perform a full dream pass for a Brain instance.
    
    Args:
        brain_db_path: Path to Brain WAL database
        distiller: GemmaDistiller instance
        batch_id: Identifier for this dream pass
        deduplicate: Whether to remove duplicates
        
    Returns:
        Number of memory candidates created
    """
    # Distill WAL entries
    candidates = distiller.distill_batch(limit=50)
    
    # Deduplicate if requested
    if deduplicate:
        candidates = distiller.deduplicate_candidates(candidates)
    
    # Simulate saving to the distiller state
    # In a real implementation, would update memory system
    # For now, just print the result
    print(f"Dream pass complete: {len(candidates)} memory candidates to process")
    for idx, candidate in enumerate(candidates[:5]):
        print(f"  [{idx+1}/{len(candidates)}] WAL {candidate.wal_id}: "
              f"confidence={candidate.confidence:.2f}, type={candidate.memory_type}")
    
    return len(candidates)


def main():
    """Run a test dream pass."""
    import sys
    sys.path.insert(0, '/mnt/hdd/brain/src')
    
    brain_db = Path("/mnt/hdd/brain/imports/curated/curated_import_wal.db")
    
    if not brain_db.exists():
        print(f"Brain database not found: {brain_db}")
        sys.exit(1)
    
    # Create distiller
    distiller = GemmaDistiller(brain_db)
    
    # Run dream pass
    count = process_dreamullan_brain(brain_db, distiller)
    
    print(f"\nTotal candidates generated: {count}")
    print("\nDream system ready for Phase 2.")


if __name__ == "__main__":
    main()

