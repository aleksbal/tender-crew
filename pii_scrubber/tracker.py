"""
Obfuscation tracking for PII anonymization.

This module provides the ObfuscationTracker class that tracks all
obfuscations with unique numbered tokens and maintains mappings
between tokens and original values.
"""

from __future__ import annotations

from typing import Dict, List


class ObfuscationTracker:
    """
    Tracks all obfuscations with unique numbered tokens.
    """
    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.mappings: List[Dict[str, str]] = []
    
    def get_next_token(self, entity_type: str) -> str:
        """Get the next numbered token for an entity type."""
        if entity_type not in self.counters:
            self.counters[entity_type] = 0
        self.counters[entity_type] += 1
        count = self.counters[entity_type]
        return f"<{entity_type}{count}>"
    
    def record_obfuscation(self, token: str, original_value: str):
        """Record an obfuscation mapping.
        
        Args:
            token: The token used in the text (e.g., "<PERSON1>")
            original_value: The original text that was obfuscated
        """
        # Extract key without angle brackets (e.g., "PERSON1" from "<PERSON1>")
        # Remove < and > if present
        key = token.strip("<>")
        self.mappings.append({
            "key": key,
            "value": original_value
        })
    
    def get_mappings(self) -> List[Dict[str, str]]:
        """Get all obfuscation mappings."""
        return self.mappings.copy()

