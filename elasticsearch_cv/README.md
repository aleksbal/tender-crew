# Elasticsearch CV Index

Elasticsearch indexing and search system for CV/resume documents with hybrid search (semantic + vector) using RRF (Reciprocal Rank Fusion).

## Features

- **Semantic Search**: Full-text keyword search across CV fields (BM25)
- **Vector Search**: Semantic similarity search using embeddings (kNN)
- **Hybrid Search**: Combines both search types using RRF
- **Technology Experience**: Aggregated experience calculation per technology
- **Structured Indexing**: Indexes CV JSON documents conforming to `cv_schema.json`

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `elasticsearch>=8.0.0` - Elasticsearch Python client
- `sentence-transformers>=2.2.0` - For generating embeddings

### 2. Start Elasticsearch

Using Docker Compose:

```bash
cd elasticsearch_cv
docker-compose up -d
```

Or using Docker directly:

```bash
docker run -d --name elasticsearch_cv \
  -p 9200:9200 \
  -e "discovery.type=single-node" \
  -e "xpack.security.enabled=false" \
  -e "ES_JAVA_OPTS=-Xms512m -Xmx512m" \
  docker.elastic.co/elasticsearch/elasticsearch:8.11.0
```

Verify Elasticsearch is running:

```bash
curl http://localhost:9200
```

### 3. Create the Index

```bash
python -m elasticsearch_cv.cli create-index
```

## Usage

### Index CV Documents

Index a single CV JSON file:

```bash
python -m elasticsearch_cv.cli index --file path/to/cv.json
```

Index all JSON files in a directory:

```bash
python -m elasticsearch_cv.cli index --directory path/to/cv_jsons/
```

The CLI automatically handles `ConversionResult` format from `text_2_json_service.py` (extracts `llm_json` field).

### Search CVs

**Hybrid search** (semantic + vector):

```bash
python -m elasticsearch_cv.cli search --query "Python developer with machine learning experience"
```

**Search by technology experience**:

```bash
python -m elasticsearch_cv.cli search --technologies "Python,Java" --min-years 3
```

**Combined search**:

```bash
python -m elasticsearch_cv.cli search \
  --query "senior software engineer" \
  --technologies "Kubernetes,Docker" \
  --min-months 12
```

**Get a specific CV**:

```bash
python -m elasticsearch_cv.cli get <document_id>
```

### Programmatic Usage

```python
from elasticsearch_cv.indexer import CVIndexer
from elasticsearch_cv.query import CVQueryService

# Index a CV
indexer = CVIndexer()
indexer.create_index()
indexer.index_cv(cv_json, file_name="cv.pdf")

# Search
query_service = CVQueryService()
results = query_service.hybrid_search(
    query_text="Python developer",
    technologies=["Python", "Django"],
    min_years=2.0,
    size=10
)
```

## Index Structure

### Semantic Search Fields

- `summary` - Professional summary
- `experience.*.company`, `experience.*.role`, `experience.*.description`
- `projects.*.project_name`, `projects.*.role_description`, `projects.*.customer`
- `skills.programming_languages`, `skills.technologies`
- `education.*.degree`, `education.*.institution`
- `certifications.*.title`

### Vector Embeddings

- `summary_embedding` - Embedding of summary text
- `experience_combined_embedding` - Combined experience descriptions
- `projects_combined_embedding` - Combined project descriptions

Embeddings use `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions) by default.

### Technology Experience

The `technology_experience` field contains aggregated data per technology:
- `technology` - Normalized technology name
- `total_months` - Total months of experience
- `experience_count` - Number of roles using this tech
- `project_count` - Number of projects using this tech
- `last_used_date` - Most recent usage date
- `is_current` - Whether currently using

## Configuration

Environment variables:

- `ELASTICSEARCH_HOST` - Elasticsearch URL (default: `http://localhost:9200`)
- `ELASTICSEARCH_INDEX_NAME` - Index name (default: `cv_index`)
- `EMBEDDING_MODEL` - Sentence transformers model (default: `sentence-transformers/all-MiniLM-L6-v2`)
- `RRF_K` - RRF rank constant (default: `60`)

## Architecture

```
CV JSON → Technology Aggregator → Calculate experience
       → Embedding Service → Generate vectors
       → Indexer → Transform & Index
       
Query → Query Service → Hybrid Search (RRF)
                      ├── Semantic Query (BM25)
                      └── Vector Query (kNN)
```

## Notes

- Technology names are normalized (lowercase) for consistent matching
- Experience is calculated from both `experience` and `projects` arrays
- Empty or missing dates are handled gracefully
- The index stores the full original JSON in `full_json` field (not analyzed)

