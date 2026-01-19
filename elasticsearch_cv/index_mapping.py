"""Elasticsearch index mapping definition for CV documents."""

from .config import EMBEDDING_DIMENSION


def get_index_mapping() -> dict:
    """Get Elasticsearch index mapping for CV documents.
    
    Returns:
        Dictionary containing the index mapping configuration
    """
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,  # Single node setup
            "refresh_interval": "1s",
            "analysis": {
                "analyzer": {
                    "default": {
                        "type": "standard"
                    },
                    "text_analyzer": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase", "stop", "snowball"]
                    }
                }
            }
        },
        "mappings": {
            "properties": {
                # Metadata
                "file_name": {
                    "type": "keyword"
                },
                "indexed_at": {
                    "type": "date"
                },
                "full_json": {
                    "type": "object",
                    "enabled": False  # Store but don't index
                },
                
                # Summary
                "summary": {
                    "type": "text",
                    "analyzer": "text_analyzer",
                    "fields": {
                        "keyword": {
                            "type": "keyword"
                        }
                    }
                },
                
                # Experience array
                "experience": {
                    "type": "nested",
                    "properties": {
                        "experience_id": {"type": "keyword"},
                        "start_date": {"type": "keyword"},
                        "end_date": {"type": "keyword"},
                        "is_current": {"type": "boolean"},
                        "company": {
                            "type": "text",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "role": {
                            "type": "text",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "location": {"type": "keyword"},
                        "employment_type": {"type": "keyword"},
                        "description": {
                            "type": "text",
                            "analyzer": "text_analyzer"
                        },
                        "technologies": {
                            "type": "keyword"
                        },
                        "evidence": {"type": "text"}
                    }
                },
                
                # Projects array
                "projects": {
                    "type": "nested",
                    "properties": {
                        "experience_id": {"type": "keyword"},
                        "start_date": {"type": "keyword"},
                        "end_date": {"type": "keyword"},
                        "project_name": {
                            "type": "text",
                            "analyzer": "text_analyzer"
                        },
                        "customer": {
                            "type": "text",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "industry": {"type": "keyword"},
                        "role": {
                            "type": "text",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "role_description": {
                            "type": "text",
                            "analyzer": "text_analyzer"
                        },
                        "technologies": {
                            "type": "keyword"
                        },
                        "evidence": {"type": "text"}
                    }
                },
                
                # Skills
                "skills": {
                    "type": "object",
                    "properties": {
                        "programming_languages": {
                            "type": "keyword"
                        },
                        "technologies": {
                            "type": "keyword"
                        },
                        "soft_skills": {
                            "type": "keyword"
                        }
                    }
                },
                
                # Education array
                "education": {
                    "type": "nested",
                    "properties": {
                        "degree": {
                            "type": "text",
                            "analyzer": "text_analyzer"
                        },
                        "institution": {
                            "type": "text",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "start_date": {"type": "keyword"},
                        "end_date": {"type": "keyword"},
                        "location": {"type": "keyword"}
                    }
                },
                
                # Certifications array
                "certifications": {
                    "type": "nested",
                    "properties": {
                        "title": {
                            "type": "text",
                            "analyzer": "text_analyzer"
                        },
                        "issuer": {"type": "keyword"},
                        "year": {"type": "integer"},
                        "evidence": {"type": "text"}
                    }
                },
                
                # Languages array
                "languages": {
                    "type": "nested",
                    "properties": {
                        "language": {"type": "keyword"},
                        "proficiency": {"type": "keyword"}
                    }
                },
                
                # Technology experience (aggregated)
                "technology_experience": {
                    "type": "nested",
                    "properties": {
                        "technology": {"type": "keyword"},
                        "total_months": {"type": "integer"},
                        "experience_count": {"type": "integer"},
                        "project_count": {"type": "integer"},
                        "last_used_date": {"type": "keyword"},
                        "is_current": {"type": "boolean"}
                    }
                },
                
                # Vector embeddings
                "summary_embedding": {
                    "type": "dense_vector",
                    "dims": EMBEDDING_DIMENSION,
                    "index": True,
                    "similarity": "cosine"
                },
                "experience_combined_embedding": {
                    "type": "dense_vector",
                    "dims": EMBEDDING_DIMENSION,
                    "index": True,
                    "similarity": "cosine"
                },
                "projects_combined_embedding": {
                    "type": "dense_vector",
                    "dims": EMBEDDING_DIMENSION,
                    "index": True,
                    "similarity": "cosine"
                }
            }
        }
    }

