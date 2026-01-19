"""Configuration for Elasticsearch CV indexing."""

import os
from typing import Optional

# Elasticsearch connection
ELASTICSEARCH_HOST: str = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
ELASTICSEARCH_INDEX_NAME: str = os.getenv("ELASTICSEARCH_INDEX_NAME", "cv_index")

# Embedding model configuration
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIMENSION: int = 384  # all-MiniLM-L6-v2 produces 384-dimensional vectors

# RRF (Reciprocal Rank Fusion) parameters
RRF_K: int = int(os.getenv("RRF_K", "60"))  # RRF rank constant

# Technology experience aggregation
TECH_EXPERIENCE_MIN_MONTHS: int = int(os.getenv("TECH_EXPERIENCE_MIN_MONTHS", "1"))

# Indexing settings
INDEX_BATCH_SIZE: int = int(os.getenv("INDEX_BATCH_SIZE", "100"))
INDEX_REFRESH_INTERVAL: str = os.getenv("INDEX_REFRESH_INTERVAL", "1s")

