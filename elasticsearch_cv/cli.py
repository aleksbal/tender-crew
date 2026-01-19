#!/usr/bin/env python3
"""CLI commands for indexing and querying CV documents in Elasticsearch."""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .indexer import CVIndexer
from .query import CVQueryService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_cv_json(file_path: str) -> Dict[str, Any]:
    """Load CV JSON from file.
    
    Args:
        file_path: Path to JSON file
        
    Returns:
        CV JSON dictionary
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Handle ConversionResult format (from text_2_json_service)
    if "llm_json" in data:
        cv_json = data["llm_json"]
        file_name = data.get("file_name", path.name)
    else:
        cv_json = data
        file_name = path.name
    
    return cv_json, file_name


def cmd_create_index(args):
    """Create the Elasticsearch index."""
    indexer = CVIndexer()
    force = args.force
    created = indexer.create_index(force=force)
    
    if created:
        print(f"✓ Index '{indexer.index_name}' created successfully")
    else:
        print(f"✓ Index '{indexer.index_name}' already exists")
        if not force:
            print("  Use --force to recreate the index")


def cmd_index(args):
    """Index CV JSON files."""
    indexer = CVIndexer()
    
    # Ensure index exists
    indexer.create_index()
    
    if args.file:
        # Index single file
        try:
            cv_json, file_name = load_cv_json(args.file)
            doc_id = indexer.index_cv(cv_json, file_name, doc_id=args.id)
            print(f"✓ Indexed: {file_name} (ID: {doc_id})")
        except Exception as e:
            logger.error(f"Failed to index {args.file}: {e}")
            sys.exit(1)
    
    elif args.directory:
        # Index all JSON files in directory
        dir_path = Path(args.directory)
        json_files = list(dir_path.glob("*.json"))
        
        if not json_files:
            print(f"No JSON files found in {args.directory}")
            return
        
        print(f"Found {len(json_files)} JSON files")
        
        documents = []
        for json_file in json_files:
            try:
                cv_json, file_name = load_cv_json(str(json_file))
                documents.append({
                    "cv_json": cv_json,
                    "file_name": file_name,
                    "doc_id": json_file.stem
                })
            except Exception as e:
                logger.warning(f"Skipping {json_file}: {e}")
        
        if documents:
            result = indexer.index_batch(documents)
            print(f"✓ Indexed {result['indexed']} documents")
            if result.get("failed", 0) > 0:
                print(f"✗ Failed: {result['failed']} documents")
                if result.get("errors"):
                    for error in result["errors"][:5]:  # Show first 5 errors
                        print(f"  - {error}")
    
    else:
        print("Error: Must specify --file or --directory")
        sys.exit(1)
    
    # Refresh index
    indexer.refresh_index()


def cmd_search(args):
    """Search CV documents."""
    query_service = CVQueryService()
    
    # Parse technologies
    technologies = None
    if args.technologies:
        technologies = [t.strip() for t in args.technologies.split(",")]
    
    # Parse min experience
    min_months = args.min_months
    min_years = args.min_years
    if min_years:
        min_months = int(min_years * 12)
    
    # Perform search
    if args.query:
        # Hybrid search
        result = query_service.hybrid_search(
            query_text=args.query,
            technologies=technologies,
            min_months=min_months,
            size=args.size,
            from_=args.from_
        )
    elif technologies:
        # Technology-only search
        result = query_service.search_by_technology(
            technologies=technologies,
            min_months=min_months,
            size=args.size,
            from_=args.from_
        )
    else:
        print("Error: Must specify --query or --technologies")
        sys.exit(1)
    
    # Display results
    hits = result.get("hits", {}).get("hits", [])
    total = result.get("hits", {}).get("total", {}).get("value", 0)
    
    print(f"\nFound {total} results (showing {len(hits)}):\n")
    
    for i, hit in enumerate(hits, 1):
        source = hit.get("_source", {})
        score = hit.get("_score", 0)
        doc_id = hit.get("_id")
        file_name = source.get("file_name", "unknown")
        
        print(f"{i}. {file_name} (ID: {doc_id}, Score: {score:.4f})")
        
        # Show summary if available
        summary = source.get("summary", "")
        if summary:
            summary_preview = summary[:150] + "..." if len(summary) > 150 else summary
            print(f"   Summary: {summary_preview}")
        
        # Show technology experience if filtered
        if technologies:
            tech_exp = source.get("technology_experience", [])
            matching_techs = [
                te for te in tech_exp
                if te.get("technology") in [t.lower() for t in technologies]
            ]
            if matching_techs:
                print("   Technology Experience:")
                for te in matching_techs[:3]:  # Show top 3
                    months = te.get("total_months", 0)
                    years = months / 12
                    print(f"     - {te.get('technology')}: {months} months ({years:.1f} years)")
        
        print()
    
    # Output JSON if requested
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_get(args):
    """Get a CV document by ID."""
    query_service = CVQueryService()
    
    doc = query_service.get_cv(args.id)
    
    if not doc:
        print(f"Document not found: {args.id}")
        sys.exit(1)
    
    if args.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        print(f"File: {doc.get('file_name', 'unknown')}")
        print(f"Indexed: {doc.get('indexed_at', 'unknown')}")
        print(f"\nSummary:\n{doc.get('summary', 'N/A')}")
        
        tech_exp = doc.get("technology_experience", [])
        if tech_exp:
            print(f"\nTechnology Experience ({len(tech_exp)} technologies):")
            for te in tech_exp[:10]:  # Show top 10
                months = te.get("total_months", 0)
                years = months / 12
                print(f"  - {te.get('technology')}: {months} months ({years:.1f} years)")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Elasticsearch CV indexing and search")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create index command
    create_parser = subparsers.add_parser("create-index", help="Create Elasticsearch index")
    create_parser.add_argument("--force", action="store_true", help="Delete existing index first")
    create_parser.set_defaults(func=cmd_create_index)
    
    # Index command
    index_parser = subparsers.add_parser("index", help="Index CV JSON files")
    index_group = index_parser.add_mutually_exclusive_group(required=True)
    index_group.add_argument("--file", help="Path to CV JSON file")
    index_group.add_argument("--directory", help="Directory containing CV JSON files")
    index_parser.add_argument("--id", help="Document ID (default: file name)")
    index_parser.set_defaults(func=cmd_index)
    
    # Search command
    search_parser = subparsers.add_parser("search", help="Search CV documents")
    search_parser.add_argument("--query", help="Text query for semantic/vector search")
    search_parser.add_argument("--technologies", help="Comma-separated list of technologies")
    search_parser.add_argument("--min-months", type=int, help="Minimum months of experience")
    search_parser.add_argument("--min-years", type=float, help="Minimum years of experience")
    search_parser.add_argument("--size", type=int, default=10, help="Number of results (default: 10)")
    search_parser.add_argument("--from", type=int, default=0, dest="from_", help="Result offset (default: 0)")
    search_parser.add_argument("--json", action="store_true", help="Output full JSON results")
    search_parser.set_defaults(func=cmd_search)
    
    # Get command
    get_parser = subparsers.add_parser("get", help="Get CV document by ID")
    get_parser.add_argument("id", help="Document ID")
    get_parser.add_argument("--json", action="store_true", help="Output full JSON")
    get_parser.set_defaults(func=cmd_get)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Command failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

