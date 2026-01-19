"""Query service for hybrid search (semantic + vector) with RRF."""

import logging
from typing import Any, Dict, List, Optional

try:
    from elasticsearch import Elasticsearch
except ImportError:
    Elasticsearch = None

from .config import ELASTICSEARCH_HOST, ELASTICSEARCH_INDEX_NAME, RRF_K
from .embedding_service import generate_embedding

logger = logging.getLogger(__name__)


class CVQueryService:
    """Service for querying CV documents with hybrid search."""
    
    def __init__(self, es_client: Optional[Any] = None):
        """Initialize the query service.
        
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
    
    def build_semantic_query(self, query_text: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Build a semantic (BM25) query for text search.
        
        Args:
            query_text: Search query text
            fields: Optional list of fields to search. If None, uses default fields.
            
        Returns:
            Elasticsearch query dictionary
        """
        if fields is None:
            fields = [
                "summary",
                "experience.description",
                "experience.company",
                "experience.role",
                "projects.role_description",
                "projects.project_name",
                "projects.customer",
                "education.degree",
                "education.institution",
                "certifications.title",
            ]
        
        return {
            "multi_match": {
                "query": query_text,
                "fields": fields,
                "type": "best_fields",
                "fuzziness": "AUTO",
                "operator": "or"
            }
        }
    
    def build_vector_query(
        self,
        query_text: str,
        embedding_field: str = "summary_embedding",
        k: int = 10
    ) -> Dict[str, Any]:
        """Build a vector (kNN) query for semantic similarity.
        
        Args:
            query_text: Search query text (will be embedded)
            embedding_field: Which embedding field to search
            k: Number of nearest neighbors to return
            
        Returns:
            Elasticsearch query dictionary
        """
        query_embedding = generate_embedding(query_text)
        
        return {
            "knn": {
                "field": embedding_field,
                "query_vector": query_embedding,
                "k": k,
                "num_candidates": k * 2  # Increase candidates for better recall
            }
        }
    
    def build_technology_filter(
        self,
        technologies: Optional[List[str]] = None,
        min_months: Optional[int] = None,
        min_years: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Build a filter for technology experience.
        
        Args:
            technologies: List of technology names to filter by
            min_months: Minimum months of experience required
            min_years: Minimum years of experience (converted to months)
            
        Returns:
            Elasticsearch filter dictionary or None
        """
        if not technologies and min_months is None and min_years is None:
            return None
        
        filters = []
        
        if technologies:
            # Normalize technology names (lowercase)
            normalized_techs = [tech.strip().lower() for tech in technologies if tech]
            if normalized_techs:
                filters.append({
                    "terms": {
                        "technology_experience.technology": normalized_techs
                    }
                })
        
        min_months_value = min_months
        if min_years is not None:
            min_months_value = int(min_years * 12)
        
        if min_months_value is not None:
            filters.append({
                "range": {
                    "technology_experience.total_months": {
                        "gte": min_months_value
                    }
                }
            })
        
        if not filters:
            return None
        
        if len(filters) == 1:
            return filters[0]
        
        return {"bool": {"must": filters}}
    
    def hybrid_search(
        self,
        query_text: str,
        technologies: Optional[List[str]] = None,
        min_months: Optional[int] = None,
        min_years: Optional[float] = None,
        size: int = 10,
        from_: int = 0,
        embedding_fields: Optional[List[str]] = None,
        semantic_fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Perform hybrid search combining semantic and vector search with RRF.
        
        Args:
            query_text: Search query text
            technologies: Optional list of technologies to filter by
            min_months: Minimum months of experience with technologies
            min_years: Minimum years of experience (alternative to min_months)
            size: Number of results to return
            from_: Offset for pagination
            embedding_fields: Which embedding fields to search (default: all)
            semantic_fields: Which text fields to search (default: all)
            
        Returns:
            Elasticsearch search results dictionary
        """
        if embedding_fields is None:
            embedding_fields = [
                "summary_embedding",
                "experience_combined_embedding",
                "projects_combined_embedding"
            ]
        
        # Build semantic query
        semantic_query = self.build_semantic_query(query_text, semantic_fields)
        
        # Build vector query (use first embedding field for simplicity)
        primary_embedding_field = embedding_fields[0] if embedding_fields else "summary_embedding"
        vector_query_dict = self.build_vector_query(query_text, primary_embedding_field, k=size)
        knn_query = vector_query_dict.get("knn", {})
        
        # Build technology filter
        tech_filter = self.build_technology_filter(technologies, min_months, min_years)
        
        # Apply filters
        if tech_filter:
            knn_query["filter"] = tech_filter
            semantic_query = {
                "bool": {
                    "must": [semantic_query],
                    "filter": [tech_filter]
                }
            }
        
        # Elasticsearch 8.x: combine query and knn - RRF is applied automatically
        # when both query and knn are present
        search_body = {
            "size": size,
            "from": from_,
            "query": semantic_query,
            "knn": knn_query,
            "rank": {
                "rrf": {
                    "window_size": size * 2,
                    "rank_constant": RRF_K
                }
            }
        }
        
        try:
            result = self.es.search(index=self.index_name, body=search_body)
            return result
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise
    
    def search_by_technology(
        self,
        technologies: List[str],
        min_months: Optional[int] = None,
        min_years: Optional[float] = None,
        size: int = 10,
        from_: int = 0
    ) -> Dict[str, Any]:
        """Search CVs by technology experience only (no text query).
        
        Args:
            technologies: List of technology names
            min_months: Minimum months of experience
            min_years: Minimum years of experience
            size: Number of results
            from_: Offset for pagination
            
        Returns:
            Elasticsearch search results dictionary
        """
        tech_filter = self.build_technology_filter(technologies, min_months, min_years)
        
        if not tech_filter:
            # No filter, return all
            query = {"match_all": {}}
        else:
            query = {"bool": {"filter": [tech_filter]}}
        
        search_body = {
            "size": size,
            "from": from_,
            "query": query,
            "sort": [
                {
                    "technology_experience.total_months": {
                        "order": "desc",
                        "nested": {
                            "path": "technology_experience",
                            "filter": tech_filter if tech_filter else {"match_all": {}}
                        }
                    }
                }
            ]
        }
        
        try:
            result = self.es.search(index=self.index_name, body=search_body)
            return result
        except Exception as e:
            logger.error(f"Technology search failed: {e}")
            raise
    
    def get_cv(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a CV document by ID.
        
        Args:
            doc_id: Document ID
            
        Returns:
            Document source or None if not found
        """
        try:
            result = self.es.get(index=self.index_name, id=doc_id)
            return result.get("_source")
        except Exception as e:
            if "not_found" in str(e).lower():
                return None
            logger.error(f"Failed to get CV {doc_id}: {e}")
            raise

