"""Calculate technology experience from CV JSON."""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> Optional[datetime]:
    """Parse YYYY-MM date string to datetime object.
    
    Args:
        date_str: Date string in YYYY-MM format, "present", or empty string
        
    Returns:
        datetime object or None if invalid/empty
    """
    if not date_str or date_str == "":
        return None
    
    if date_str == "present":
        return datetime.now()
    
    try:
        return datetime.strptime(date_str, "%Y-%m")
    except ValueError:
        logger.warning(f"Invalid date format: {date_str}")
        return None


def calculate_months_between(start_date: Optional[datetime], end_date: Optional[datetime]) -> int:
    """Calculate number of months between two dates.
    
    Args:
        start_date: Start datetime or None
        end_date: End datetime or None (uses current date if None)
        
    Returns:
        Number of months (0 if dates are invalid)
    """
    if not start_date:
        return 0
    
    if not end_date:
        end_date = datetime.now()
    
    if end_date < start_date:
        logger.warning(f"End date {end_date} is before start date {start_date}")
        return 0
    
    # Calculate months difference
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    
    # Add partial month if end day is later in month
    if end_date.day > start_date.day:
        months += 1
    
    return max(0, months)


def normalize_technology(tech: str) -> str:
    """Normalize technology name for consistent matching.
    
    Args:
        tech: Technology name string
        
    Returns:
        Normalized technology name (lowercase, stripped)
    """
    return tech.strip().lower() if tech else ""


def aggregate_technology_experience(cv_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Aggregate technology experience from CV JSON.
    
    Calculates total months of experience per technology from both
    experience entries and projects. Also tracks usage counts and
    most recent usage dates.
    
    Args:
        cv_json: CV JSON object conforming to cv_schema.json
        
    Returns:
        List of technology experience objects with:
        - technology: normalized technology name
        - total_months: total months of experience
        - experience_count: number of experience entries using this tech
        - project_count: number of projects using this tech
        - last_used_date: most recent end_date (YYYY-MM format)
        - is_current: whether currently using this technology
    """
    tech_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "total_months": 0,
        "experience_count": 0,
        "project_count": 0,
        "last_used_date": None,
        "is_current": False,
        "last_used_datetime": None,
    })
    
    # Process experience entries
    experience_list = cv_json.get("experience", [])
    for exp in experience_list:
        if not isinstance(exp, dict):
            continue
        
        start_date = parse_date(exp.get("start_date", ""))
        end_date = parse_date(exp.get("end_date", ""))
        months = calculate_months_between(start_date, end_date)
        is_current = exp.get("is_current", False) or (exp.get("end_date", "") == "present")
        
        technologies = exp.get("technologies", [])
        if not isinstance(technologies, list):
            technologies = []
        
        for tech in technologies:
            normalized = normalize_technology(tech)
            if not normalized:
                continue
            
            tech_data[normalized]["total_months"] += months
            tech_data[normalized]["experience_count"] += 1
            
            if is_current:
                tech_data[normalized]["is_current"] = True
            
            # Track most recent usage
            if end_date:
                current_last = tech_data[normalized]["last_used_datetime"]
                if not current_last or end_date > current_last:
                    tech_data[normalized]["last_used_datetime"] = end_date
                    tech_data[normalized]["last_used_date"] = exp.get("end_date", "")
    
    # Process projects
    projects_list = cv_json.get("projects", [])
    for project in projects_list:
        if not isinstance(project, dict):
            continue
        
        start_date = parse_date(project.get("start_date", ""))
        end_date = parse_date(project.get("end_date", ""))
        months = calculate_months_between(start_date, end_date)
        
        technologies = project.get("technologies", [])
        if not isinstance(technologies, list):
            technologies = []
        
        for tech in technologies:
            normalized = normalize_technology(tech)
            if not normalized:
                continue
            
            tech_data[normalized]["total_months"] += months
            tech_data[normalized]["project_count"] += 1
            
            # Track most recent usage
            if end_date:
                current_last = tech_data[normalized]["last_used_datetime"]
                if not current_last or end_date > current_last:
                    tech_data[normalized]["last_used_datetime"] = end_date
                    tech_data[normalized]["last_used_date"] = project.get("end_date", "")
    
    # Convert to list format and filter out technologies with no valid experience
    result = []
    for tech_name, data in tech_data.items():
        if data["total_months"] > 0 or data["experience_count"] > 0 or data["project_count"] > 0:
            result.append({
                "technology": tech_name,
                "total_months": data["total_months"],
                "experience_count": data["experience_count"],
                "project_count": data["project_count"],
                "last_used_date": data["last_used_date"] or "",
                "is_current": data["is_current"],
            })
    
    # Sort by total_months descending
    result.sort(key=lambda x: x["total_months"], reverse=True)
    
    return result

