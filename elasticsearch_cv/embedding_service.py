"""Generate embeddings for CV text fields."""

import logging
from typing import Any, Dict, List, Optional

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from .config import EMBEDDING_DIMENSION, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_model_cache: Optional[Any] = None


def get_embedding_model():
    """Get or load the embedding model (cached)."""
    global _model_cache
    
    if _model_cache is not None:
        return _model_cache
    
    if SentenceTransformer is None:
        raise ImportError(
            "sentence-transformers is not installed. "
            "Install it with: pip install sentence-transformers"
        )
    
    logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
    _model_cache = SentenceTransformer(EMBEDDING_MODEL)
    logger.info(f"Embedding model loaded. Dimension: {EMBEDDING_DIMENSION}")
    
    return _model_cache


def generate_embedding(text: str) -> List[float]:
    """Generate embedding for a single text string.
    
    Args:
        text: Text to embed
        
    Returns:
        List of floats representing the embedding vector
    """
    if not text or not text.strip():
        # Return zero vector for empty text
        return [0.0] * EMBEDDING_DIMENSION
    
    model = get_embedding_model()
    embedding = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    return embedding.tolist()


def generate_cv_embeddings(cv_json: Dict[str, Any]) -> Dict[str, List[float]]:
    """Generate embeddings for all relevant CV fields.
    
    Args:
        cv_json: CV JSON object conforming to cv_schema.json
        
    Returns:
        Dictionary with embedding fields:
        - summary_embedding: embedding of summary text
        - experience_combined_embedding: combined experience descriptions
        - projects_combined_embedding: combined project descriptions
    """
    embeddings = {}
    
    # Summary embedding
    summary = cv_json.get("summary", "")
    if summary:
        embeddings["summary_embedding"] = generate_embedding(summary)
    else:
        embeddings["summary_embedding"] = [0.0] * EMBEDDING_DIMENSION
    
    # Combined experience descriptions
    experience_list = cv_json.get("experience", [])
    experience_texts = []
    for exp in experience_list:
        if not isinstance(exp, dict):
            continue
        description = exp.get("description", "")
        if description:
            experience_texts.append(description)
    
    if experience_texts:
        combined_experience = " ".join(experience_texts)
        embeddings["experience_combined_embedding"] = generate_embedding(combined_experience)
    else:
        embeddings["experience_combined_embedding"] = [0.0] * EMBEDDING_DIMENSION
    
    # Combined project descriptions
    projects_list = cv_json.get("projects", [])
    project_texts = []
    for project in projects_list:
        if not isinstance(project, dict):
            continue
        role_description = project.get("role_description", "")
        if role_description:
            project_texts.append(role_description)
    
    if project_texts:
        combined_projects = " ".join(project_texts)
        embeddings["projects_combined_embedding"] = generate_embedding(combined_projects)
    else:
        embeddings["projects_combined_embedding"] = [0.0] * EMBEDDING_DIMENSION
    
    return embeddings

