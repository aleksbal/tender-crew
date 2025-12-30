"""
cv_pii_scrubber.py

IT CV text anonymizer (DE/EN) using a hybrid approach.

Pipeline (deterministic, auditable):
  1) Normalize text for stable matching (apostrophes, hyphens, NBSP)
  2) Presidio Analyzer (spaCy NER) + custom regex recognizers for high-precision PII
  3) First anonymization pass with Presidio spans
  4) PrimaryIdentityResolver selects candidate's primary name using:
     - NameCandidateExtractor: extracts from Presidio, headers, emails, LinkedIn
     - NameScorer: scores candidates with configurable weights (replaces magic numbers)
     - NameVariantGenerator: generates variants (handles middle names, hyphenated names, reversed order)
  5) Propagate masking for chosen name variants + initials
  6) Combined URL policy + postprocessing pass (single traversal for efficiency)
  7) Optional debug output: chosen identity, scoring breakdown, masked variants/patterns

Architecture improvements:
  - Phone detection: stricter regex pattern excludes date formats (no post-filtering needed)
  - PatternRegistry: centralized regex patterns for easier maintenance
  - AddressFilter: consolidated address detection logic
  - Modular identity resolution: separate extractor, scorer, and variant generator classes
  - Reduced passes: URL policy and postprocessing combined into single pass

Dependencies:
  pip install presidio-analyzer presidio-anonymizer spacy
  python -m spacy download de_core_news_md
  python -m spacy download en_core_web_lg
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Set
from urllib.parse import urlparse

from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig


# -----------------------------
# Config
# -----------------------------

@dataclass(frozen=True)
class AnonymizeConfig:
    supported_languages: Tuple[str, ...] = ("de", "en")
    spacy_models: Tuple[Tuple[str, str], ...] = (
        ("de", "de_core_news_md"),
        ("en", "en_core_web_lg"),
    )

    target_entities: Tuple[str, ...] = (
        "PERSON",
        "ADDRESS",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "LINKEDIN_PROFILE",
        "URL",
    )

    run_both_lang_passes: bool = True

    # Identity resolution / propagation
    propagate_primary_name: bool = True
    enable_initials: bool = True
    min_name_token_len: int = 3
    min_lastname_len_for_initials: int = 3


    # URL policy:
    # "redact_all" | "keep_domain" | "allowlist_domains_keep_domain"
    url_policy: str = "keep_domain"
    url_domain_allowlist: Tuple[str, ...] = ("linkedin.com", "www.linkedin.com")

    debug: bool = False

    # Limit PII obfuscation to first N characters (0 = no limit, process entire text)
    pii_obfuscation_limit: int = 0

    # Override Presidio anonymizer operators if you want custom tokens
    operators: Optional[dict] = None


# -----------------------------
# Normalization + helpers
# -----------------------------

def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _safe_lower(s: str) -> str:
    return s.casefold()


def _span_overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def normalize_text_for_matching(text: str) -> str:
    """
    Normalize punctuation/whitespace so regex propagation is stable.
    """
    t = text
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("‐", "-").replace("–", "-").replace("—", "-")
    t = re.sub(r"[\u00A0\u2007\u202F]", " ", t)  # NBSP variants
    return t


def _strip_name_token(token: str) -> str:
    """
    Keep letters/digits/underscore + German letters + hyphen + apostrophe.
    """
    return re.sub(r"[^\wÄÖÜäöüß\-']", "", token)


# -----------------------------
# Pattern Registry
# -----------------------------

class PatternRegistry:
    """
    Centralized registry for all regex patterns used in PII detection.
    Makes patterns easier to maintain, test, and tune.
    """
    
    # Email patterns
    EMAIL_STANDARD = r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b"
    EMAIL_OBFUSCATED = r"\b[a-zA-Z0-9_.+-]+\s*(?:\(|\[)?\s*(?:at|@)\s*(?:\)|\])?\s*[a-zA-Z0-9-]+\s*(?:\(|\[)?\s*(?:dot|\.)\s*(?:\)|\])?\s*[a-zA-Z0-9-.]+\b"
    
    # Phone pattern: stricter to avoid matching dates
    # Note: We use a simpler pattern and validate in post-processing to exclude dates
    PHONE_STRICT = (
        r"\b(?:\+?\d{1,4}[\s().-]?)?(?:\(?\d{2,5}\)?[\s().-]?)?\d{2,4}[\s().-]?\d{2,4}[\s().-]?\d{2,6}\b"
    )
    
    # Date patterns to exclude from phone detection
    DATE_MONTH_YEAR = re.compile(r"\b\d{1,2}[/.]\s*(?:19|20)\d{2}\b")  # 2/2025, 12.1998
    DATE_YEAR_MONTH = re.compile(r"\b(?:19|20)\d{2}[/.]\s*\d{1,2}\b")  # 2025/2, 1998.12
    DATE_YEAR_RANGE = re.compile(r"\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b")  # 2025-2026
    DATE_MONTHYEAR_RANGE = re.compile(r"\b\d{1,2}[/.](?:19|20)\d{2}\s*[-–]\s*\d{1,2}[/.](?:19|20)\d{2}\b")  # 2/2025 - 12/2025
    
    # German address patterns
    STREET_TYPES = r"(?:straße|strasse|str\.|weg|allee|gasse|platz|ring|damm|ufer)"
    ADDRESS_FULL_DE = rf"\b[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{2,}}?{STREET_TYPES}\s+\d{{1,4}}[a-zA-Z]?\s*,?\s*\d{{5}}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\.\- ]{{1,}}\b"
    ADDRESS_ZIP_CITY_DE = r"\b\d{5}\s+[A-ZÄÖÜ][\wÄÖÜäöüß\- ]{2,}\b"
    
    # LinkedIn patterns
    LINKEDIN_PROFILE = r"\b(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/(?:in|pub)/[A-Za-z0-9\-_%/]+/?\b"
    LINKEDIN_HANDLE_EXTRACT = r"linkedin\.com/(?:in|pub)/([^/?#\s]+)"
    
    # URL patterns
    URL_SCHEME = r"\bhttps?://[^\s<>()\[\]\"']{6,}\b"
    URL_BARE = r"\b(?:www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s<>()\[\]\"']*)?\b"
    URL_FIND = rf"{URL_SCHEME}|{URL_BARE}"
    
    # Name patterns
    TITLE_CASE_WORD = r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)?$"
    TITLE_CASE_COMPOUND = r"^[A-ZÄÖÜ][a-zäöüß]+(?:[-'][A-ZÄÖÜa-zäöüß]+)+$"
    
    # Contact zone detection
    CONTACT_ZONE = r"\b(contact|kontakt|address|adresse|email|e-mail|phone|telefon)\b"


# -----------------------------
# Address Filter
# -----------------------------

class AddressFilter:
    """
    Centralized address detection logic to avoid scattered checks.
    """
    _STREET_PATTERN = re.compile(r"(straße|strasse|str\.)\b", flags=re.IGNORECASE)
    _ZIP_CODE_PATTERN = re.compile(r"\b\d{5}\b")
    
    @classmethod
    def contains_markers(cls, text: str) -> bool:
        """
        Check if text contains address markers (street names or German zip codes).
        """
        return bool(cls._STREET_PATTERN.search(text)) or bool(cls._ZIP_CODE_PATTERN.search(text))
    
    @classmethod
    def should_exclude(cls, text: str) -> bool:
        """
        Determine if text should be excluded because it looks like an address.
        """
        return cls.contains_markers(text)


# -----------------------------
# City Name Filter
# -----------------------------

class CityNameFilter:
    """
    Filters out city names that are misclassified as PERSON entities.
    """
    # Common German and international cities
    CITY_NAMES = {
        # German cities
        "mainz", "hamburg", "berlin", "münchen", "munich", "köln", "cologne",
        "frankfurt", "stuttgart", "düsseldorf", "dortmund", "essen", "leipzig",
        "bremen", "dresden", "hannover", "nürnberg", "nuremberg", "duisburg",
        "bochum", "wuppertal", "bielefeld", "bonn", "münster", "karlsruhe",
        "mannheim", "augsburg", "wiesbaden", "gelsenkirchen", "mönchengladbach",
        "braunschweig", "chemnitz", "kiel", "aachen", "halle", "magdeburg",
        "freiburg", "krefeld", "lübeck", "oberhausen", "erfurt", "mainz",
        "rostock", "kassel", "hagen", "hamm", "saarbrücken", "mülheim",
        "potsdam", "ludwigshafen", "oldenburg", "leverkusen", "osnabrück",
        "solingen", "heidelberg", "herne", "neuss", "darmstadt", "paderborn",
        "regensburg", "ingolstadt", "würzburg", "fürth", "wolfsburg", "offenbach",
        "ulm", "heilbronn", "pforzheim", "göttingen", "bottrop", "trier",
        "recklinghausen", "reutlingen", "bremerhaven", "koblenz", "bergisch",
        "jena", "remscheid", "erlangen", "moers", "siegen", "hildesheim",
        "salzgitter", "cottbus", "gütersloh", "kaiserslautern", "schwerin",
        "witten", "gera", "isenburg", "zwickau", "düren", "ratingen",
        
        # International cities (common in CVs)
        "london", "paris", "madrid", "rome", "amsterdam", "brussels", "vienna",
        "zurich", "stockholm", "copenhagen", "oslo", "helsinki", "warsaw",
        "prague", "budapest", "bucharest", "sofia", "athens", "lisbon",
        "dublin", "edinburgh", "glasgow", "birmingham", "manchester", "liverpool",
        "barcelona", "milan", "naples", "turin", "genoa", "florence", "venice",
        "rotterdam", "the hague", "utrecht", "eindhoven", "groningen",
        "brussels", "antwerp", "ghent", "bruges", "lyon", "marseille", "toulouse",
        "nice", "nantes", "strasbourg", "bordeaux", "lille", "rennes",
        "vienna", "salzburg", "graz", "linz", "innsbruck", "zurich", "geneva",
        "basel", "bern", "lausanne", "stockholm", "gothenburg", "malmo",
        "copenhagen", "aarhus", "odense", "oslo", "bergen", "trondheim",
        "helsinki", "tampere", "turku", "warsaw", "krakow", "gdansk", "wroclaw",
        "prague", "brno", "ostrava", "budapest", "debrecen", "szeged",
        "bucharest", "cluj", "timisoara", "sofia", "plovdiv", "varna",
        "athens", "thessaloniki", "patras", "lisbon", "porto", "coimbra",
        "dublin", "cork", "galway", "limerick",
        
        # US cities (common in tech CVs)
        "new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
        "san antonio", "san diego", "dallas", "san jose", "austin", "jacksonville",
        "san francisco", "indianapolis", "columbus", "fort worth", "charlotte",
        "seattle", "denver", "washington", "boston", "el paso", "detroit",
        "nashville", "portland", "oklahoma city", "las vegas", "memphis",
        "louisville", "baltimore", "milwaukee", "albuquerque", "tucson",
        "fresno", "sacramento", "kansas city", "mesa", "atlanta", "omaha",
        "colorado springs", "raleigh", "miami", "long beach", "virginia beach",
        "oakland", "minneapolis", "tulsa", "tampa", "cleveland", "wichita",
        "arlington", "new orleans", "honolulu",
    }
    
    @classmethod
    def is_city_name(cls, text: str) -> bool:
        """
        Check if text is a known city name.
        """
        text_lower = _safe_lower(text.strip())
        # Check exact match
        if text_lower in cls.CITY_NAMES:
            return True
        # Check if text contains a city name (for compound names like "New York")
        words = re.split(r"[\s\-_]+", text_lower)
        return any(word in cls.CITY_NAMES for word in words)
    
    @classmethod
    def should_exclude(cls, text: str) -> bool:
        """
        Determine if text should be excluded because it's a city name.
        """
        return cls.is_city_name(text)


# -----------------------------
# Technology Filter
# -----------------------------

class TechnologyFilter:
    """
    Filters out technology/product names and common false positives from PERSON entities.
    """
    # Common technology/product names that are often misclassified as PERSON or LOCATION
    TECHNOLOGY_NAMES = {
        # Programming languages
        "java", "python", "javascript", "typescript", "node", "react", "angular", "vue",
        "c", "c++", "c#", "go", "rust", "ruby", "php", "swift", "kotlin", "scala",
        "groovy", "perl", "r", "matlab", "sql", "html", "css", "scss", "sass",
        
        # Databases
        "oracle", "mysql", "postgresql", "mongodb", "redis", "kafka", "elasticsearch",
        "cassandra", "dynamodb", "couchdb", "neo4j", "influxdb", "timescaledb",
        "db2", "sqlite", "mariadb",
        
        # Build tools & package managers
        "maven", "gradle", "npm", "yarn", "pip", "composer", "poetry", "cargo",
        "ant", "make", "cmake", "bazel", "buck", "pants",
        
        # CI/CD & DevOps
        "jenkins", "gitlab", "github", "bitbucket", "bamboo", "teamcity", "circleci",
        "travis", "azure devops", "azure pipelines", "argocd", "spinnaker",
        "git", "svn", "mercurial", "perforce",
        
        # Containers & Orchestration
        "docker", "kubernetes", "helm", "kustomize", "rancher", "openshift",
        "docker compose", "podman", "containerd",
        
        # Cloud platforms
        "aws", "azure", "gcp", "google cloud", "alibaba cloud", "oracle cloud",
        "digitalocean", "linode", "vultr", "heroku", "vercel", "netlify",
        
        # Infrastructure as Code
        "terraform", "ansible", "puppet", "chef", "saltstack", "cloudformation",
        "pulumi", "cdk", "serverless",
        
        # Monitoring & Observability
        "prometheus", "grafana", "opentelemetry", "new relic", "datadog", "splunk",
        "elastic", "elk", "kibana", "logstash", "graylog", "zabbix", "nagios",
        "instana", "dynatrace", "appdynamics", "sentry", "rollbar",
        
        # Security & Auth
        "keycloak", "oauth", "oauth2", "saml", "ldap", "vault", "consul",
        "cert-manager", "istio", "linkerd",
        
        # API tools
        "swagger", "openapi", "postman", "insomnia", "graphql", "rest", "soap",
        "grpc", "protobuf", "protocol buffers",
        
        # Testing frameworks
        "junit", "testcontainers", "jest", "jasmine", "karma", "mocha", "cypress",
        "selenium", "playwright", "pytest", "rspec", "testng", "spock",
        "wiremock", "mockito", "sinon", "nock",
        
        # Linting & Formatting
        "eslint", "prettier", "stylelint", "husky", "lint-staged", "black",
        "flake8", "pylint", "mypy", "rubocop", "gofmt", "clang-format",
        
        # Build tools & bundlers
        "webpack", "rollup", "vite", "parcel", "browserify", "gulp", "grunt",
        "lombok", "mapstruct", "querydsl",
        
        # Frameworks & Libraries
        "spring", "spring boot", "spring cloud", "spring cloud stream", "cloud stream",
        "django", "flask", "fastapi", "express", "rails", "laravel", "symfony",
        "dropwizard", "micronaut", "quarkus", "vertx", "akka",
        "hibernate", "jpa", "liquibase", "flyway", "alembic",
        "rxjs", "lodash", "underscore", "jquery", "bootstrap", "tailwind",
        "thymeleaf", "jsp", "jsf", "vaadin",
        
        # AI/ML
        "crew ai", "crew", "openai", "anthropic", "langchain", "llama",
        "tensorflow", "pytorch", "keras", "scikit-learn", "pandas", "numpy",
        
        # Operating systems
        "linux", "windows", "macos", "ubuntu", "debian", "centos", "redhat",
        "fedora", "suse", "arch", "alpine", "freebsd", "openbsd",
        
        # Web servers
        "apache", "nginx", "tomcat", "jetty", "wildfly", "jboss", "glassfish",
        "iis", "caddy", "traefik",
        
        # Message queues
        "kafka", "rabbitmq", "activemq", "artemis", "pulsar", "nats", "zeromq",
        
        # Version control & collaboration
        "jira", "confluence", "slack", "teams", "zoom", "mattermost", "discord",
        "trello", "asana", "notion",
        
        # Enterprise software
        "salesforce", "sap", "workday", "servicenow", "oracle ebs", "peoplesoft",
        
        # Other tools
        "sonarqube", "codeclimate", "coveralls", "codacy", "intellij idea",
        "eclipse", "vscode", "vim", "emacs", "sublime",
        "gnu make", "piral", "qemu", "kvm", "proxmox", "openvz", "openstack",
        "xen", "esx", "vmware", "virtualbox", "vagrant",
    }
    
    # Common false positives (non-name words that appear in CVs)
    FALSE_POSITIVES = {
        "office", "mobile", "phone", "email", "address", "contact",
        "project", "projects", "experience", "skills", "education",
        "summary", "objective", "profile", "curriculum", "vitae",
        "lebenslauf", "resume", "cv",
    }
    
    @classmethod
    def _get_exclude_set(cls) -> Set[str]:
        """Get combined exclude set (lazy initialization)."""
        return cls.TECHNOLOGY_NAMES | cls.FALSE_POSITIVES
    
    @classmethod
    def is_technology_name(cls, text: str) -> bool:
        """
        Check if text is a known technology/product name.
        """
        text_lower = _safe_lower(text.strip())
        exclude_set = cls._get_exclude_set()
        # Check exact match
        if text_lower in exclude_set:
            return True
        # Check if any word in the text is a tech name
        words = re.split(r"[\s\-_]+", text_lower)
        return any(word in exclude_set for word in words)
    
    @classmethod
    def should_exclude(cls, text: str) -> bool:
        """
        Determine if text should be excluded because it's a technology name or false positive.
        """
        return cls.is_technology_name(text)




# -----------------------------
# Name Candidate Extractor
# -----------------------------

class NameCandidateExtractor:
    """
    Extracts name candidates from various sources (Presidio, headers, emails, LinkedIn).
    """
    _STOPWORDS = {
        "curriculum", "vitae", "lebenslauf", "profil", "profile", "summary",
        "kontakt", "contact", "information", "info", "adresse", "address",
        "telefon", "phone", "email", "e-mail", "linkedin", "github", "portfolio",
        "website", "webseite", "blog",
        "senior", "junior", "engineer", "developer", "architect", "consultant",
        "principal", "platform", "cloud", "data", "services",
        "gmbh", "ag", "inc", "ltd", "llc", "company", "university", "universität",
        "prof", "prof.", "dr", "dr.", "mr", "mrs", "ms",
    }
    _TITLE_CASE_WORD = re.compile(PatternRegistry.TITLE_CASE_WORD)

    def extract_all(
        self,
        text: str,
        presidio_results: Sequence[RecognizerResult],
        extracted_urls: Sequence[str],
        extracted_emails: Sequence[str],
    ) -> List[Dict]:
        """Extract candidates from all sources."""
        text_norm = normalize_text_for_matching(text)
        lines = text_norm.splitlines()
        address_spans = [(r.start, r.end) for r in presidio_results if r.entity_type == "ADDRESS"]

        candidates: List[Dict] = []
        candidates += self.from_person_spans(text_norm, presidio_results, address_spans)
        candidates += self.from_header_lines(lines)
        candidates += self.from_emails(extracted_emails)
        candidates += self.from_linkedin(extracted_urls)
        
        # Apply context-aware filtering (tech stack contexts)
        candidates = self._filter_tech_stack_context(candidates, text_norm)
        
        return candidates
    
    def _filter_tech_stack_context(self, candidates: List[Dict], text: str) -> List[Dict]:
        """
        Filter out candidates that appear in tech stack contexts (comma-separated lists, "used tech:" etc.)
        """
        filtered: List[Dict] = []
        text_lower = text.lower()
        
        for cand in candidates:
            name = cand.get("name", "")
            name_lower = _safe_lower(name)
            
            # Check if this appears in a tech stack context
            # Look for patterns like "used tech:", "technologies:", "tech stack:", etc.
            # Include German patterns: "Eingesetzte Technologien:", "Technologien:", etc.
            tech_context_patterns = [
                r"used\s+tech[:\s]",
                r"technologies[:\s]",
                r"tech\s+stack[:\s]",
                r"skills[:\s]",
                r"tools[:\s]",
                r"eingesetzte\s+technologien[:\s]",  # German: "Used technologies"
                r"technologien[:\s]",  # German: "Technologies"
                r"verwendete\s+technologien[:\s]",  # German: "Technologies used"
                r"technologien\s+und\s+tools[:\s]",  # German: "Technologies and tools"
                r"verwendete\s+technik[:\s]",  # German: "Technology used"
                r"werkzeuge[:\s]",  # German: "Tools"
                r"software[:\s]",  # German: "Software"
                r"frameworks[:\s]",  # German/English: "Frameworks"
                r"bibliotheken[:\s]",  # German: "Libraries"
            ]
            
            # Find all occurrences of this name in the text
            name_pattern = re.escape(name)
            matches = list(re.finditer(rf"\b{name_pattern}\b", text, flags=re.IGNORECASE))
            
            # Check if any match is in a tech context
            in_tech_context = False
            for match in matches:
                start = match.start()
                # Look backwards for tech context indicators
                context_start = max(0, start - 100)
                context = text[context_start:start + len(name) + 50].lower()
                
                for pattern in tech_context_patterns:
                    if re.search(pattern, context):
                        in_tech_context = True
                        break
                
                # Also check if it's in a comma-separated list (likely tech stack)
                # Look for pattern: word, word, word (at least 2 commas nearby)
                list_context = text[max(0, start - 50):min(len(text), start + len(name) + 50)]
                comma_count = list_context.count(',')
                if comma_count >= 2:
                    # Likely a tech stack list
                    in_tech_context = True
                    break
            
            if not in_tech_context:
                filtered.append(cand)
        
        return filtered

    def from_person_spans(
        self,
        text: str,
        results: Sequence[RecognizerResult],
        address_spans: Sequence[Tuple[int, int]],
    ) -> List[Dict]:
        """Extract candidates from Presidio PERSON entities."""
        out: List[Dict] = []
        for r in results:
            if r.entity_type != "PERSON":
                continue
            if any(_span_overlaps(r.start, r.end, a0, a1) for a0, a1 in address_spans):
                continue
            frag = _norm_space(text[r.start:r.end])
            if not frag or len(frag) < 3:
                continue
            if AddressFilter.should_exclude(frag):
                continue
            # Filter out technology names
            if TechnologyFilter.should_exclude(frag):
                continue
            
            # Clean name boundaries - remove common labels/prefixes
            cleaned_frag = self._clean_name_boundaries(frag, text, r.start, r.end)
            if not cleaned_frag or len(cleaned_frag) < 3:
                continue
            
            out.append({
                "name": cleaned_frag,
                "source": "presidio_person",
                "meta": {"start": r.start, "end": r.end, "score": r.score},
            })
        return out

    def _clean_name_boundaries(self, frag: str, full_text: str, start: int, end: int) -> str:
        """
        Clean name boundaries to remove common labels/prefixes like "Office:", "Mobile:", etc.
        """
        # Common labels that shouldn't be part of names
        label_patterns = [
            r"^(office|mobile|phone|email|address|contact|tel|fax)[:\s]*",
            r"[:\s]*(office|mobile|phone|email|address|contact|tel|fax)$",
        ]
        
        cleaned = frag
        for pattern in label_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Split into words and filter out label words
        words = cleaned.split()
        filtered_words = []
        label_words = {"office", "mobile", "phone", "email", "address", "contact", "tel", "fax"}
        
        for word in words:
            word_clean = _strip_name_token(word)
            if word_clean and _safe_lower(word_clean) not in label_words:
                filtered_words.append(word_clean)
        
        # If we removed words, return the cleaned version
        if len(filtered_words) < len(words):
            return " ".join(filtered_words)
        
        return cleaned.strip()

    def from_header_lines(self, lines: List[str]) -> List[Dict]:
        """Extract candidates from header lines (first 20 lines)."""
        out: List[Dict] = []
        contact_zone_start: Optional[int] = None
        for i, raw in enumerate(lines[:60]):
            if re.search(PatternRegistry.CONTACT_ZONE, raw, flags=re.IGNORECASE):
                contact_zone_start = i
                break

        max_header = 20 if contact_zone_start is None else min(20, contact_zone_start)

        for i, raw in enumerate(lines[:max_header]):
            line = _norm_space(raw)
            if not line or len(line) > 70:
                continue
            if AddressFilter.should_exclude(line):
                continue

            words = [w for w in re.split(r"\s+", line) if w]
            name_like: List[str] = []
            for w in words:
                w_clean = _strip_name_token(normalize_text_for_matching(w))
                if not w_clean:
                    continue
                if self._TITLE_CASE_WORD.match(w_clean) or re.match(PatternRegistry.TITLE_CASE_COMPOUND, w_clean):
                    if _safe_lower(w_clean) not in self._STOPWORDS:
                        name_like.append(w_clean)

            if len(name_like) >= 2:
                cand = " ".join(name_like[:4])
                if AddressFilter.should_exclude(cand):
                    continue
                # Filter out technology names
                if TechnologyFilter.should_exclude(cand):
                    continue
                out.append({
                    "name": cand,
                    "source": "header_line",
                    "meta": {"line_idx": i, "line": line},
                })
        return out

    def from_emails(self, emails: Sequence[str]) -> List[Dict]:
        """Extract candidates from email local parts."""
        out: List[Dict] = []
        for e in emails:
            local = e.split("@", 1)[0]
            parts = re.split(r"[._\-+]+", local)
            parts = [p for p in parts if p and len(p) >= 2 and not p.isdigit()]

            if any(p.lower() in {"info", "kontakt", "contact", "hr", "jobs", "career", "bewerbung"} for p in parts):
                continue

            if len(parts) >= 2:
                cand = f"{parts[0].capitalize()} {parts[1].capitalize()}"
                out.append({"name": cand, "source": "email_localpart", "meta": {"email": e, "local": local}})
            elif len(parts) == 1:
                out.append({"name": parts[0].capitalize(), "source": "email_localpart_weak", "meta": {"email": e, "local": local}})
        return out

    def from_linkedin(self, urls: Sequence[str]) -> List[Dict]:
        """Extract candidates from LinkedIn profile URLs."""
        out: List[Dict] = []
        for u in urls:
            handle = self._extract_linkedin_handle(u)
            if not handle:
                continue
            handle = re.sub(r"\d+$", "", handle)
            parts = [p for p in re.split(r"[-_]+", handle) if p and len(p) >= 2]
            if len(parts) >= 2:
                cand = f"{parts[0].capitalize()} {parts[1].capitalize()}"
            else:
                cand = parts[0].capitalize() if parts else ""
            if cand:
                out.append({"name": cand, "source": "linkedin_handle", "meta": {"url": u, "handle": handle}})
        return out

    @staticmethod
    def _extract_linkedin_handle(url: str) -> Optional[str]:
        m = re.search(PatternRegistry.LINKEDIN_HANDLE_EXTRACT, url, flags=re.IGNORECASE)
        return m.group(1) if m else None


# -----------------------------
# Name Scorer
# -----------------------------

class NameScorer:
    """
    Scores name candidates using configurable weights.
    Replaces magic numbers with named constants.
    """
    # Source weights
    SOURCE_WEIGHTS = {
        "presidio_person": 60.0,
        "header_line": 40.0,
        "linkedin_handle": 18.0,
        "email_localpart": 14.0,
        "email_localpart_weak": 6.0,
        "default": 5.0,
    }
    
    # Token count bonuses/penalties
    TOKEN_COUNT_OPTIMAL_MIN = 2
    TOKEN_COUNT_OPTIMAL_MAX = 4
    TOKEN_COUNT_OPTIMAL_BONUS = 25.0
    TOKEN_COUNT_SINGLE_BONUS = 5.0
    TOKEN_COUNT_TOO_MANY_PENALTY = -10.0
    
    # Stopword penalty
    STOPWORD_PENALTY_PER_TOKEN = 25.0
    
    # Header position bonuses/penalties
    HEADER_POSITION_BONUS_BASE = 25.0
    HEADER_POSITION_BONUS_DECAY = 3.0
    HEADER_POSITION_LATE_PENALTY = -15.0
    HEADER_POSITION_LATE_THRESHOLD = 6
    
    # Presidio score bonus
    PRESIDIO_SCORE_MULTIPLIER = 20.0
    PRESIDIO_SCORE_MAX_BONUS = 20.0
    
    # Frequency bonus
    FREQUENCY_MIN_OCCURRENCES = 2
    FREQUENCY_BONUS_PER_TOKEN = 5.0
    FREQUENCY_MAX_BONUS = 20.0
    
    # Digits penalty
    DIGITS_PENALTY = -30.0
    
    # Common word penalty (for words like Office, Mobile, etc.)
    COMMON_WORD_PENALTY = -40.0
    COMMON_WORDS = {"office", "mobile", "phone", "email", "address", "contact", "tel", "fax"}
    
    # Proper name bonus (title case, 2-4 tokens, no digits, no common words)
    PROPER_NAME_BONUS = 15.0

    def __init__(self, config: AnonymizeConfig):
        self.config = config
        self._stopwords = NameCandidateExtractor._STOPWORDS

    def score_all(self, candidates: List[Dict], text: str) -> List[Dict]:
        """Score all candidates and return sorted by score."""
        scored: List[Dict] = []
        for c in candidates:
            name = normalize_text_for_matching(_norm_space(c["name"]))
            if AddressFilter.should_exclude(name):
                continue

            tokens = [_strip_name_token(t) for t in re.split(r"\s+", name) if t]
            tokens = [t for t in tokens if t]

            breakdown: Dict[str, float] = {}
            score = 0.0

            # Source weight
            src = c.get("source", "")
            src_weight = self.SOURCE_WEIGHTS.get(src, self.SOURCE_WEIGHTS["default"])
            score += src_weight
            breakdown["source_weight"] = src_weight

            # Token count bonus/penalty
            if self.TOKEN_COUNT_OPTIMAL_MIN <= len(tokens) <= self.TOKEN_COUNT_OPTIMAL_MAX:
                score += self.TOKEN_COUNT_OPTIMAL_BONUS
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_OPTIMAL_BONUS
            elif len(tokens) == 1:
                score += self.TOKEN_COUNT_SINGLE_BONUS
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_SINGLE_BONUS
            else:
                score += self.TOKEN_COUNT_TOO_MANY_PENALTY
                breakdown["token_count_bonus"] = self.TOKEN_COUNT_TOO_MANY_PENALTY

            # Stopword penalty
            stop_pen = 0.0
            for t in tokens:
                if _safe_lower(t) in self._stopwords:
                    stop_pen += self.STOPWORD_PENALTY_PER_TOKEN
            if stop_pen:
                score -= stop_pen
                breakdown["stopword_penalty"] = -stop_pen

            # Header position bonus
            if src == "header_line":
                li = int(c.get("meta", {}).get("line_idx", 999))
                if li <= self.HEADER_POSITION_LATE_THRESHOLD:
                    pos_bonus = max(0, self.HEADER_POSITION_BONUS_BASE - (li * self.HEADER_POSITION_BONUS_DECAY))
                    score += pos_bonus
                    breakdown["position_bonus"] = pos_bonus
                else:
                    score += self.HEADER_POSITION_LATE_PENALTY
                    breakdown["late_header_penalty"] = self.HEADER_POSITION_LATE_PENALTY

            # Presidio score bonus
            if src == "presidio_person":
                ps = float(c.get("meta", {}).get("score", 0.0))
                ps_bonus = min(self.PRESIDIO_SCORE_MAX_BONUS, ps * self.PRESIDIO_SCORE_MULTIPLIER)
                score += ps_bonus
                breakdown["presidio_score_bonus"] = ps_bonus

            # Frequency bonus
            freq_bonus = 0.0
            for t in tokens:
                if len(t) < self.config.min_name_token_len:
                    continue
                cnt = len(re.findall(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE))
                if cnt >= self.FREQUENCY_MIN_OCCURRENCES:
                    freq_bonus += self.FREQUENCY_BONUS_PER_TOKEN
            if freq_bonus:
                score += min(self.FREQUENCY_MAX_BONUS, freq_bonus)
                breakdown["frequency_bonus"] = min(self.FREQUENCY_MAX_BONUS, freq_bonus)

            # Digits penalty
            if re.search(r"\d", name):
                score += self.DIGITS_PENALTY
                breakdown["digits_penalty"] = self.DIGITS_PENALTY

            # Common word penalty (Office, Mobile, etc.)
            name_lower = _safe_lower(name)
            has_common_word = any(word in self.COMMON_WORDS for word in name_lower.split())
            if has_common_word:
                score += self.COMMON_WORD_PENALTY
                breakdown["common_word_penalty"] = self.COMMON_WORD_PENALTY

            # Proper name bonus: title case, 2-4 tokens, no digits, no common words
            if (2 <= len(tokens) <= 4 and
                not re.search(r"\d", name) and
                not has_common_word and
                all(t[0].isupper() if t else False for t in tokens if t)):
                score += self.PROPER_NAME_BONUS
                breakdown["proper_name_bonus"] = self.PROPER_NAME_BONUS

            scored.append({
                **c,
                "name": name,
                "tokens": tokens,
                "score_total": score,
                "score_breakdown": breakdown,
            })

        # Deduplicate by name (keep highest scoring)
        best_by_name: Dict[str, Dict] = {}
        for c in scored:
            key = _safe_lower(c["name"])
            if key not in best_by_name or c["score_total"] > best_by_name[key]["score_total"]:
                best_by_name[key] = c

        return list(best_by_name.values())


# -----------------------------
# Name Variant Generator
# -----------------------------

class NameVariantGenerator:
    """
    Generates name variants and initials patterns for masking.
    """
    _STOPWORDS = NameCandidateExtractor._STOPWORDS

    def __init__(self, config: AnonymizeConfig):
        self.config = config

    def derive_variants(self, chosen_name: str, full_text: str) -> Set[str]:
        """Generate all variants of a name for masking."""
        chosen_name = normalize_text_for_matching(chosen_name)
        tokens_raw = [t for t in re.split(r"\s+", chosen_name) if t]

        clean: List[str] = []
        for t in tokens_raw:
            t2 = _strip_name_token(t)
            if len(t2) < self.config.min_name_token_len:
                continue
            if _safe_lower(t2) in self._STOPWORDS:
                continue
            clean.append(t2)

        if not clean:
            return set()

        variants: Set[str] = set()
        full = " ".join(clean)
        variants.add(full)
        variants.add(clean[0])
        if len(clean) >= 2:
            variants.add(clean[-1])
            # Add middle names if present
            if len(clean) > 2:
                for middle in clean[1:-1]:
                    variants.add(middle)

        # Handle hyphenated names
        for token in clean:
            if "-" in token:
                parts = token.split("-")
                for part in parts:
                    if len(part) >= self.config.min_name_token_len:
                        variants.add(part)

        # First name prefixes
        first = clean[0]
        for k in range(4, len(first)):
            pref = first[:k]
            if pref.endswith("-") or pref.endswith("'"):
                continue
            if re.search(rf"\b{re.escape(pref)}\b", full_text, flags=re.IGNORECASE):
                variants.add(pref)

        # Reversed name order (last, first)
        if len(clean) >= 2:
            reversed_name = f"{clean[-1]} {clean[0]}"
            variants.add(reversed_name)

        return {v for v in variants if len(v) >= self.config.min_name_token_len}

    def build_initials_patterns(self, variants: Set[str], full_text: str) -> List[str]:
        """Build regex patterns for matching initials."""
        full = max(variants, key=len, default="")
        toks = [t for t in full.split() if t]
        if len(toks) < 2:
            return []
        first, last = toks[0], toks[-1]
        if len(last) < self.config.min_lastname_len_for_initials:
            return []

        fi = re.escape(first[0].upper())
        li = re.escape(last[0].upper())
        last_esc = re.escape(last)

        patterns: List[str] = []
        patterns.append(rf"\b{fi}\.?\s+{last_esc}\b")                 # M. O'Connell
        patterns.append(rf"\b{fi}\.\s*{li}\.(?=\b|[\s,;:])")         # M.O. (stable with punctuation)

        if re.search(rf"\b{fi}\.\s*{li}\b", full_text):
            patterns.append(rf"\b{fi}\.\s*{li}\b")                   # M.O

        if re.search(rf"\b{fi}\s+{li}\b", full_text):
            patterns.append(rf"\b{fi}\s+{li}\b")                     # M O

        return patterns


# -----------------------------
# Primary Identity Resolver
# -----------------------------

class PrimaryIdentityResolver:
    def __init__(self, config: AnonymizeConfig):
        self.config = config
        self.extractor = NameCandidateExtractor()
        self.scorer = NameScorer(config)
        self.variant_generator = NameVariantGenerator(config)

    def resolve(
        self,
        text: str,
        presidio_results: Sequence[RecognizerResult],
        extracted_urls: Sequence[str],
        extracted_emails: Sequence[str],
    ) -> Dict:
        """Resolve primary identity using extractor, scorer, and variant generator."""
        text_norm = normalize_text_for_matching(text)

        # Extract candidates from all sources
        candidates = self.extractor.extract_all(
            text_norm, presidio_results, extracted_urls, extracted_emails
        )

        # Score candidates
        scored = self.scorer.score_all(candidates, text_norm)
        chosen = max(scored, key=lambda c: c["score_total"], default=None)

        # Generate variants and initials patterns
        variants: Set[str] = set()
        initials_patterns: List[str] = []
        chosen_name = ""

        if chosen and chosen.get("name"):
            chosen_name = chosen["name"]
            variants = self.variant_generator.derive_variants(chosen_name, text_norm)
            if self.config.enable_initials:
                initials_patterns = self.variant_generator.build_initials_patterns(variants, text_norm)

        debug_info = {
            "chosen_name": chosen_name,
            "chosen_source": chosen.get("source") if chosen else None,
            "chosen_score_total": chosen.get("score_total") if chosen else None,
            "chosen_score_breakdown": chosen.get("score_breakdown") if chosen else None,
            "candidates_top": sorted(scored, key=lambda c: c["score_total"], reverse=True)[:10],
            "masked_variants": sorted(variants, key=len, reverse=True),
            "masked_initials_patterns": initials_patterns,
        }

        return {
            "chosen_name": chosen_name,
            "variants": variants,
            "initials_patterns": initials_patterns,
            "debug": debug_info,
        }



# -----------------------------
# CvAnonymizer
# -----------------------------

class CvAnonymizer:
    _ENTITY_PRIORITY = {
        "ADDRESS": 100,
        "LINKEDIN_PROFILE": 95,
        "EMAIL_ADDRESS": 90,
        "PHONE_NUMBER": 80,
        "URL": 75,
        "PERSON": 70,
        "LOCATION": 10,
    }

    _URL_FIND_RE = re.compile(PatternRegistry.URL_FIND)

    def __init__(self, config: Optional[AnonymizeConfig] = None):
        self.config = config or AnonymizeConfig()
        self.analyzer = self._build_analyzer()
        self.anonymizer = AnonymizerEngine()
        self.identity_resolver = PrimaryIdentityResolver(self.config)

        if self.config.operators is None:
            self.operators = {
                "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"}),
                "PERSON": OperatorConfig("replace", {"new_value": "<PERSON>"}),
                "ADDRESS": OperatorConfig("replace", {"new_value": "<ADDRESS>"}),
                "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<EMAIL>"}),
                "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "<PHONE>"}),
                "LINKEDIN_PROFILE": OperatorConfig("replace", {"new_value": "<LINKEDIN>"}),
                "URL": OperatorConfig("replace", {"new_value": "<URL>"}),
                "LOCATION": OperatorConfig("replace", {"new_value": "<LOCATION>"}),
            }
        else:
            self.operators = self.config.operators

    def anonymize(self, text: str, preferred_language: str = "de") -> str:
        if not text:
            return text

        # Apply PII obfuscation limit if configured
        if self.config.pii_obfuscation_limit > 0 and len(text) > self.config.pii_obfuscation_limit:
            # Only anonymize the first N characters
            text_to_anonymize = text[:self.config.pii_obfuscation_limit]
            text_remainder = text[self.config.pii_obfuscation_limit:]
            
            # Anonymize only the first part
            anonymized_part = self._anonymize_text(text_to_anonymize, preferred_language)
            
            # Return anonymized part + original remainder
            return anonymized_part + text_remainder
        else:
            # Process entire text (no limit)
            return self._anonymize_text(text, preferred_language)
    
    def _anonymize_text(self, text: str, preferred_language: str = "de") -> str:
        """
        Internal method that performs the actual anonymization.
        Separated to allow limiting obfuscation to a portion of text.
        """
        if not text:
            return text

        text_norm = normalize_text_for_matching(text)

        # 1) Analyze (multi-lang)
        results = self._analyze_multi_pass(text_norm, preferred_language=preferred_language)

        # 2) Filter out phone numbers that are actually dates
        results = self._filter_date_like_phones(results, text_norm)
        
        # 2b) Filter out PERSON entities that are technology names
        results = self._filter_technology_persons(results, text_norm)

        # 3) Collapse overlaps (priority-based)
        collapsed = self._collapse_overlaps(results, text_norm)

        extracted_urls = self._extract_entity_texts(text_norm, results, {"URL", "LINKEDIN_PROFILE"})
        extracted_emails = self._extract_entity_texts(text_norm, results, {"EMAIL_ADDRESS"})

        # 4) First anonymization pass
        anon = self.anonymizer.anonymize(text=text_norm, analyzer_results=collapsed, operators=self.operators)
        out = anon.text

        # 5) Resolve primary identity + propagate variants / initials
        resolved = self.identity_resolver.resolve(
            text=text_norm,
            presidio_results=results,
            extracted_urls=extracted_urls,
            extracted_emails=extracted_emails,
        )

        if self.config.propagate_primary_name and resolved["variants"]:
            out = self._mask_variants(out, resolved["variants"], label="<PERSON>")

        if self.config.enable_initials and resolved["initials_patterns"]:
            out = self._mask_initials(out, resolved["initials_patterns"], label="<PERSON>")

        # 6) Combined URL policy + postprocessing pass
        out = self._apply_url_policy_and_postprocess(out)

        # 7) Debug
        if self.config.debug:
            self._print_debug(resolved)

        return out

    # -----------------------
    # Phone date filtering
    # -----------------------

    def _filter_date_like_phones(self, results: Sequence[RecognizerResult], text: str) -> List[RecognizerResult]:
        """
        Filter out PHONE_NUMBER entities that are actually dates.
        This is a post-filter to catch date patterns that the regex didn't exclude.
        """
        if not results:
            return []

        out: List[RecognizerResult] = []
        for r in results:
            if r.entity_type != "PHONE_NUMBER":
                out.append(r)
                continue

            span_text = text[r.start:r.end]
            
            # Check if this looks like a date using the date patterns
            if (PatternRegistry.DATE_MONTH_YEAR.search(span_text) or
                PatternRegistry.DATE_YEAR_MONTH.search(span_text) or
                PatternRegistry.DATE_YEAR_RANGE.search(span_text) or
                PatternRegistry.DATE_MONTHYEAR_RANGE.search(span_text)):
                # This is a date, not a phone number
                continue

            # Also check if the span contains a year pattern (19xx or 20xx)
            if re.search(r"\b(?:19|20)\d{2}\b", span_text):
                # Contains a year, likely a date
                continue
            
            out.append(r)
        
        return out
    
    def _filter_technology_persons(self, results: Sequence[RecognizerResult], text: str) -> List[RecognizerResult]:
        """
        Filter out PERSON and LOCATION entities that are actually technology/product names or city names.
        This prevents tech names and cities from being incorrectly masked.
        """
        if not results:
            return []
        
        out: List[RecognizerResult] = []
        for r in results:
            # Filter PERSON entities that are cities or tech names
            if r.entity_type == "PERSON":
                span_text = _norm_space(text[r.start:r.end])
                
                # Check if this is a city name (misclassified as PERSON)
                if CityNameFilter.should_exclude(span_text):
                    # This is a city, not a person - but keep it as LOCATION would be correct
                    # Actually, we should exclude it from PERSON masking
                    continue
                
                # Check if this is a technology name
                if TechnologyFilter.should_exclude(span_text):
                    # This is a tech name, not a person
                    continue
                
                # Also check context - if it's in a tech stack context, exclude it
                context_start = max(0, r.start - 50)
                context_end = min(len(text), r.end + 50)
                context = text[context_start:context_end].lower()
                
                tech_context_patterns = [
                    r"used\s+tech[:\s]",
                    r"technologies[:\s]",
                    r"tech\s+stack[:\s]",
                    r"skills[:\s]",
                    r"tools[:\s]",
                    r"eingesetzte\s+technologien[:\s]",  # German: "Used technologies"
                    r"technologien[:\s]",  # German: "Technologies"
                ]
                
                in_tech_context = any(re.search(pattern, context) for pattern in tech_context_patterns)
                if in_tech_context:
                    # In tech context, likely not a person name
                    continue
            
            # Filter LOCATION entities that are technology names
            elif r.entity_type == "LOCATION":
                span_text = _norm_space(text[r.start:r.end])
                
                # Check if this is a technology name (misclassified as LOCATION)
                if TechnologyFilter.should_exclude(span_text):
                    # This is a tech name, not a location
                    continue

            out.append(r)

        return out

    # -----------------------
    # Combined URL policy + postprocessing
    # -----------------------

    def _apply_url_policy_and_postprocess(self, text: str) -> str:
        """
        Combined pass: applies URL policy and postprocessing in a single traversal.
        More efficient than separate passes.
        """
        if not text:
            return text

        # First apply URL policy
        policy = self.config.url_policy

        def url_repl(m: re.Match) -> str:
            raw = m.group(0)
            if raw.startswith("<") and raw.endswith(">"):
                return raw

            domain, has_path = self._parse_domain_and_path(raw)
            if not domain:
                return "<URL>"

            if policy == "redact_all":
                return "<URL>"

            if policy == "keep_domain":
                return f"{domain}/<PATH>" if has_path else domain

            if policy == "allowlist_domains_keep_domain":
                if domain in self.config.url_domain_allowlist:
                    return f"{domain}/<PATH>" if has_path else domain
                return "<URL>"

            return "<URL>"

        out = self._URL_FIND_RE.sub(url_repl, text)

        # Then apply postprocessing cleanup
        tags = {
            "<PERSON>",
            "<ADDRESS>",
            "<EMAIL>",
            "<PHONE>",
            "<LINKEDIN>",
            "<URL>",
            "<LOCATION>",
            "<REDACTED>",
        }

        # Collapse repeats for each token (individually)
        for token in tags:
            out = re.sub(rf"(?:{re.escape(token)}[\s]*){{2,}}", token + " ", out)

        # Remove trailing spaces before newlines
        out = re.sub(r"[ \t]+\n", "\n", out)

        # Spacing before punctuation
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        out = re.sub(r"([,.;:!?])([A-Za-z0-9<])", r"\1 \2", out)

        # "( <TAG> )" -> "(<TAG>)"
        out = re.sub(r"\(\s+(<[^>]+>)\s+\)", r"(\1)", out)

        # Collapse large empty blocks
        out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"

        return out

    def postprocess(self, text: str) -> str:
        """
        Legacy method for backward compatibility.
        Delegates to combined method (URL policy with default settings).
        """
        return self._apply_url_policy_and_postprocess(text)

    # -----------------------
    # Analyzer + recognizers
    # -----------------------

    def _build_analyzer(self) -> AnalyzerEngine:
        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": lang, "model_name": model} for lang, model in self.config.spacy_models],
        }
        provider = NlpEngineProvider(nlp_configuration=configuration)
        nlp_engine = provider.create_engine()

        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=list(self.config.supported_languages))
        self._add_regex_recognizers(analyzer)
        return analyzer

    def _add_regex_recognizers(self, analyzer: AnalyzerEngine) -> None:
        """Add all regex-based recognizers using patterns from PatternRegistry."""
        
        # Email addresses
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="EMAIL_ADDRESS",
                supported_language="en",
                patterns=[
                    Pattern("email", PatternRegistry.EMAIL_STANDARD, 0.95),
                    Pattern("email_obf", PatternRegistry.EMAIL_OBFUSCATED, 0.75),
                ],
            )
        )

        # Phone numbers (stricter pattern to avoid dates)
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="PHONE_NUMBER",
                supported_language="en",
                patterns=[
                    Pattern("phone_strict", PatternRegistry.PHONE_STRICT, 0.85)
                ],
            )
        )

        # German addresses
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="ADDRESS",
                supported_language="de",
                patterns=[
                    Pattern("de_address_full", PatternRegistry.ADDRESS_FULL_DE, 0.85),
                    Pattern("de_zip_city", PatternRegistry.ADDRESS_ZIP_CITY_DE, 0.50),
                ],
            )
        )

        # LinkedIn profiles
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="LINKEDIN_PROFILE",
                supported_language="en",
                patterns=[
                    Pattern("linkedin_profile_any", PatternRegistry.LINKEDIN_PROFILE, 0.95)
                ],
            )
        )

        # URLs
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="URL",
                supported_language="en",
                patterns=[
                    Pattern("url_scheme", PatternRegistry.URL_SCHEME, 0.85),
                    Pattern("url_bare", PatternRegistry.URL_BARE, 0.70),
                ],
            )
        )

    # -----------------------
    # Analyze passes
    # -----------------------

    def _analyze_multi_pass(self, text: str, preferred_language: str) -> List[RecognizerResult]:
        langs = list(self.config.supported_languages)
        if preferred_language not in langs:
            preferred_language = langs[0]

        ordered = [preferred_language]
        if self.config.run_both_lang_passes:
            ordered += [l for l in langs if l != preferred_language]

        merged: List[RecognizerResult] = []
        for lang in ordered:
            merged.extend(self.analyzer.analyze(text=text, entities=list(self.config.target_entities), language=lang))
        return merged

    # -----------------------
    # Overlap collapse
    # -----------------------

    def _collapse_overlaps(self, results: Sequence[RecognizerResult], original_text: str) -> List[RecognizerResult]:
        if not results:
            return []

        sorted_results = sorted(results, key=lambda r: (r.start, -r.end, -(r.end - r.start), -r.score))
        out: List[RecognizerResult] = []

        for r in sorted_results:
            span_len = r.end - r.start
            if span_len > 160:
                continue
            if "\n" in original_text[r.start:r.end]:
                continue

            if not out:
                out.append(self._clone(r))
                continue

            last = out[-1]
            if r.start <= last.end:
                if self._better(r, last):
                    out[-1] = self._clone(r)
            else:
                out.append(self._clone(r))

        return out

    def _better(self, cand: RecognizerResult, curr: RecognizerResult) -> bool:
        cp = self._ENTITY_PRIORITY.get(cand.entity_type, 0)
        rp = self._ENTITY_PRIORITY.get(curr.entity_type, 0)
        if cp != rp:
            return cp > rp
        if cand.score != curr.score:
            return cand.score > curr.score
        return (cand.end - cand.start) > (curr.end - curr.start)

    @staticmethod
    def _clone(r: RecognizerResult) -> RecognizerResult:
        return RecognizerResult(entity_type=r.entity_type, start=r.start, end=r.end, score=r.score)

    # -----------------------
    # Propagation helpers
    # -----------------------

    def _mask_variants(self, text: str, variants: Set[str], label: str) -> str:
        out = text
        for v in sorted(variants, key=len, reverse=True):
            out = re.sub(rf"\b{re.escape(v)}\b", label, out, flags=re.IGNORECASE)
        return out

    def _mask_initials(self, text: str, patterns: Sequence[str], label: str) -> str:
        out = text
        for pat in patterns:
            out = re.sub(pat, label, out)
        return out


    @staticmethod
    def _parse_domain_and_path(raw: str) -> Tuple[Optional[str], bool]:
        s = raw.strip()
        if not re.match(r"^https?://", s, flags=re.IGNORECASE):
            s_for_parse = "http://" + s
        else:
            s_for_parse = s

        try:
            p = urlparse(s_for_parse)
            netloc = (p.netloc or "").lower()
            if not netloc:
                return None, False
            domain = netloc[4:] if netloc.startswith("www.") else netloc
            has_path = bool((p.path and p.path != "/") or p.query)
            return domain, has_path
        except Exception:
            return None, False

    # -----------------------
    # Entity text extraction
    # -----------------------

    @staticmethod
    def _extract_entity_texts(text: str, results: Sequence[RecognizerResult], types: Set[str]) -> List[str]:
        out: List[str] = []
        for r in results:
            if r.entity_type in types:
                frag = _norm_space(text[r.start:r.end])
                if frag:
                    out.append(frag)

        seen = set()
        uniq: List[str] = []
        for x in out:
            k = x.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq

    # -----------------------
    # Debug
    # -----------------------

    def _print_debug(self, resolved: Dict) -> None:
        dbg = resolved.get("debug", {})
        print("\n=== CV ANONYMIZER DEBUG ===")
        print(f"Chosen candidate name: {dbg.get('chosen_name')!r}")
        print(f"Chosen source: {dbg.get('chosen_source')}")
        print(f"Chosen total score: {dbg.get('chosen_score_total')}")
        print("Why chosen (score breakdown):")
        bd = dbg.get("chosen_score_breakdown") or {}
        for k, v in bd.items():
            print(f"  - {k}: {v}")

        print("\nTop candidates (score):")
        for c in dbg.get("candidates_top", []):
            print(
                f"  * {c['name']!r}  score={c['score_total']:.1f}  "
                f"source={c.get('source')}  breakdown={c.get('score_breakdown')}"
            )

        print("\nMasked tokens/variants:")
        for v in dbg.get("masked_variants", []):
            print(f"  - {v}")

        print("\nMasked initials patterns:")
        for p in dbg.get("masked_initials_patterns", []):
            print(f"  - {p}")
        print("===========================\n")


# -----------------------------
# Demo
# -----------------------------
if __name__ == "__main__":
    sample = """
Lebenslauf
    
Aleksandar Herman Balaban     
Office: 06221 / 123456
Mobile: +49 171 2345678

Online Producer/Webdeveloper
selbstständig freiberuflich

Projects:
    
2/2025 - 12/2025
My last Java project    
used tech: Java, Python, JavaScript, Oracle, MySQL, Kafka, Anthropic    
2/1997 - 12/1998
My first Java project für eine 'Duale Hochschule

Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL
2/1997 - 12/1998
My first Java project
Verwendung von Prometheus für tracking
Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL, Gradle    
"""
    anon = CvAnonymizer(AnonymizeConfig(debug=True, url_policy="keep_domain"))
    print(anon.anonymize(sample, preferred_language="de"))
