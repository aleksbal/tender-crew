"""Index CV documents to Elasticsearch."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from elasticsearch import Elasticsearch
    from elasticsearch.exceptions import RequestError
except ImportError:
    Elasticsearch = None
    RequestError = None

from .config import ELASTICSEARCH_HOST, ELASTICSEARCH_INDEX_NAME
from .embedding_service import generate_cv_embeddings
from .index_mapping import get_index_mapping
from .technology_aggregator import aggregate_technology_experience

logger = logging.getLogger(__name__)


class CVIndexer:
    """Service for indexing CV documents to Elasticsearch."""
    
    def __init__(self, es_client: Optional[Any] = None):
        """Initialize the indexer.
        
        Args:
            es_client: Optional Elasticsearch client. If None, creates one from config.
        """
        if Elasticsearch is None:
            raise ImportError(
                "elasticsearch is not installed. "
                "Install it with: pip install elasticsearch"
            )
        
        self.es = es_client or Elasticsearch([ELASTICSEARCH_HOST])
        self.index_name = ELASTICSEARCH_INDEX_NAME
        
        # Verify connection
        try:
            if not self.es.ping():
                raise ConnectionError(f"Cannot connect to Elasticsearch at {ELASTICSEARCH_HOST}")
        except Exception as e:
            logger.error(f"Failed to connect to Elasticsearch: {e}")
            raise
    
    def create_index(self, force: bool = False) -> bool:
        """Create the CV index if it doesn't exist.
        
        Args:
            force: If True, delete existing index first
            
        Returns:
            True if index was created, False if it already exists
        """
        if self.es.indices.exists(index=self.index_name):
            if force:
                logger.info(f"Deleting existing index: {self.index_name}")
                self.es.indices.delete(index=self.index_name)
            else:
                logger.info(f"Index {self.index_name} already exists")
                return False
        
        mapping = get_index_mapping()
        logger.info(f"Creating index: {self.index_name}")
        self.es.indices.create(index=self.index_name, **mapping)
        logger.info(f"Index {self.index_name} created successfully")
        return True
    
    def transform_cv_to_document(self, cv_json: Dict[str, Any], file_name: str) -> Dict[str, Any]:
        """Transform CV JSON to Elasticsearch document format.
        
        Args:
            cv_json: CV JSON object conforming to cv_schema.json
            file_name: Source file name
            
        Returns:
            Elasticsearch document dictionary
        """
        # Calculate technology experience
        tech_experience = aggregate_technology_experience(cv_json)
        
        # Generate embeddings
        embeddings = generate_cv_embeddings(cv_json)
        
        # Build document
        doc = {
            "file_name": file_name,
            "indexed_at": datetime.utcnow().isoformat(),
            "full_json": cv_json,  # Store original JSON
            "summary": cv_json.get("summary", ""),
            "experience": cv_json.get("experience", []),
            "projects": cv_json.get("projects", []),
            "skills": cv_json.get("skills", {}),
            "education": cv_json.get("education", []),
            "certifications": cv_json.get("certifications", []),
            "languages": cv_json.get("languages", []),
            "technology_experience": tech_experience,
            **embeddings,
        }
        
        return doc
    
    def index_cv(self, cv_json: Dict[str, Any], file_name: str, doc_id: Optional[str] = None) -> str:
        """Index a single CV document.
        
        Args:
            cv_json: CV JSON object conforming to cv_schema.json
            file_name: Source file name
            doc_id: Optional document ID. If None, uses file_name as ID.
            
        Returns:
            Document ID
        """
        if doc_id is None:
            doc_id = file_name
        
        doc = self.transform_cv_to_document(cv_json, file_name)
        
        try:
            self.es.index(index=self.index_name, id=doc_id, document=doc)
            logger.info(f"Indexed CV: {file_name} (ID: {doc_id})")
            return doc_id
        except Exception as e:
            logger.error(f"Failed to index CV {file_name}: {e}")
            raise
    
    def index_batch(self, cv_documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Index multiple CV documents in a batch.
        
        Args:
            cv_documents: List of dicts with 'cv_json' and 'file_name' keys
            
        Returns:
            Dictionary with indexing results
        """
        from elasticsearch.helpers import bulk
        
        actions = []
        for item in cv_documents:
            cv_json = item.get("cv_json")
            file_name = item.get("file_name", "unknown")
            doc_id = item.get("doc_id", file_name)
            
            if not cv_json:
                logger.warning(f"Skipping document with missing cv_json: {file_name}")
                continue
            
            doc = self.transform_cv_to_document(cv_json, file_name)
            actions.append({
                "_index": self.index_name,
                "_id": doc_id,
                "_source": doc
            })
        
        if not actions:
            logger.warning("No documents to index")
            return {"indexed": 0, "errors": []}
        
        try:
            success, failed = bulk(self.es, actions, raise_on_error=False)
            logger.info(f"Bulk indexed {success} documents, {len(failed)} failed")
            
            return {
                "indexed": success,
                "failed": len(failed),
                "errors": failed
            }
        except Exception as e:
            logger.error(f"Bulk indexing failed: {e}")
            raise
    
    def delete_cv(self, doc_id: str) -> bool:
        """Delete a CV document from the index.
        
        Args:
            doc_id: Document ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        try:
            result = self.es.delete(index=self.index_name, id=doc_id, ignore=[404])
            deleted = result.get("result") == "deleted"
            if deleted:
                logger.info(f"Deleted CV document: {doc_id}")
            else:
                logger.info(f"CV document not found: {doc_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete CV {doc_id}: {e}")
            raise
    
    def refresh_index(self):
        """Refresh the index to make recent changes searchable."""
        self.es.indices.refresh(index=self.index_name)
        logger.debug(f"Refreshed index: {self.index_name}")

