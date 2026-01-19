#!/usr/bin/env python3
"""Example usage of the Elasticsearch CV indexing and search system."""

import json
from elasticsearch_cv.indexer import CVIndexer
from elasticsearch_cv.query import CVQueryService

# Example CV JSON (minimal structure)
example_cv = {
    "personal_info": {
        "first_name": "John",
        "last_name": "Doe",
        "phone": "",
        "email": ""
    },
    "summary": "Experienced Python developer with expertise in machine learning and cloud technologies.",
    "experience": [
        {
            "start_date": "2020-01",
            "end_date": "present",
            "company": "Tech Corp",
            "role": "Senior Software Engineer",
            "description": "Developed ML models using Python and TensorFlow. Led team of 5 developers.",
            "technologies": ["Python", "TensorFlow", "AWS", "Docker"],
            "evidence": "",
            "is_current": True
        },
        {
            "start_date": "2018-06",
            "end_date": "2019-12",
            "company": "Startup Inc",
            "role": "Software Engineer",
            "description": "Built REST APIs using Django and PostgreSQL.",
            "technologies": ["Python", "Django", "PostgreSQL"],
            "evidence": "",
            "is_current": False
        }
    ],
    "education": [
        {
            "degree": "B.Sc. Computer Science",
            "institution": "University",
            "start_date": "2014-09",
            "end_date": "2018-06",
            "location": "City"
        }
    ],
    "skills": {
        "programming_languages": ["Python", "JavaScript"],
        "technologies": ["AWS", "Docker", "Kubernetes"],
        "soft_skills": ["Leadership", "Communication"]
    },
    "projects": [
        {
            "start_date": "2021-01",
            "end_date": "2021-06",
            "project_name": "ML Platform",
            "customer": "Client A",
            "industry": "Finance",
            "role": "Lead Developer",
            "role_description": "Built end-to-end ML platform for fraud detection.",
            "technologies": ["Python", "TensorFlow", "Kubernetes"],
            "evidence": ""
        }
    ],
    "certifications": [],
    "languages": [
        {"language": "English", "proficiency": "Native"}
    ]
}


def main():
    """Example workflow."""
    print("=== Elasticsearch CV Indexing Example ===\n")
    
    # Initialize services
    print("1. Initializing services...")
    indexer = CVIndexer()
    query_service = CVQueryService()
    
    # Create index
    print("2. Creating index...")
    indexer.create_index(force=True)
    
    # Index example CV
    print("3. Indexing example CV...")
    doc_id = indexer.index_cv(example_cv, file_name="example_cv.json")
    print(f"   Indexed with ID: {doc_id}\n")
    
    # Refresh index
    indexer.refresh_index()
    
    # Search examples
    print("4. Performing searches...\n")
    
    # Hybrid search
    print("   a) Hybrid search: 'Python machine learning'")
    results = query_service.hybrid_search("Python machine learning", size=5)
    print(f"      Found {results['hits']['total']['value']} results\n")
    
    # Technology search
    print("   b) Technology search: Python with 2+ years")
    results = query_service.search_by_technology(
        technologies=["Python"],
        min_years=2.0,
        size=5
    )
    print(f"      Found {results['hits']['total']['value']} results\n")
    
    # Combined search
    print("   c) Combined search: 'engineer' + Docker + 1+ year")
    results = query_service.hybrid_search(
        query_text="engineer",
        technologies=["Docker"],
        min_years=1.0,
        size=5
    )
    print(f"      Found {results['hits']['total']['value']} results\n")
    
    # Get document
    print("5. Retrieving document...")
    doc = query_service.get_cv(doc_id)
    if doc:
        tech_exp = doc.get("technology_experience", [])
        print(f"   Technology experience entries: {len(tech_exp)}")
        for te in tech_exp[:3]:
            print(f"     - {te['technology']}: {te['total_months']} months")
    
    print("\n=== Example completed ===")


if __name__ == "__main__":
    main()

