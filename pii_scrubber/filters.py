"""
Filter classes for excluding false positives from PII detection.

This module contains filter classes that help identify and exclude
false positives such as city names, technology names, and addresses
that might be misclassified as PERSON or LOCATION entities.
"""

import re
from typing import Set

from .utils import safe_lower


class AddressFilter:
    """
    Centralized address detection logic to avoid scattered checks.
    """
    _STREET_PATTERN = re.compile(r"(street|straße|strasse|str\.)\b", flags=re.IGNORECASE)
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
        # Additional German cities (medium-sized, common in CVs)
        "bayreuth", "bamberg", "weimar", "fulda", "marburg", "giessen",
        "konstanz", "freiburg im breisgau", "tübingen", "ulm", "ravensburg",
        "baden-baden", "heidelberg", "mannheim", "ludwigshafen", "wiesbaden",
        "mainz", "darmstadt", "offenbach", "hanau", "aschaffenburg",
        "würzburg", "schweinfurt", "coburg", "hof", "ansbach", "nürnberg",
        "erlangen", "fürth", "bamberg", "bayreuth", "regensburg", "passau",
        "landshut", "rosenheim", "traunstein", "kempten", "memmingen",
        "augsburg", "kaufbeuren", "neu-ulm", "günzburg", "donauwörth",
        
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
        text_lower = safe_lower(text.strip())
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
        # English
        "office", "mobile", "phone", "email", "address", "contact",
        "project", "projects", "experience", "skills", "education",
        "summary", "objective", "profile", "curriculum", "vitae",
        "resume", "cv",
        # German
        "büro", "mobil", "telefon", "e-mail", "adresse", "kontakt",
        "projekt", "projekte", "erfahrung", "fähigkeiten", "bildung",
        "zusammenfassung", "ziel", "profil", "lebenslauf",
        # Common section headers (both languages)
        "arbeit", "beruf", "karriere", "work", "career", "employment",
        "ausbildung", "bildung", "studium", "education", "studies",
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
        text_lower = safe_lower(text.strip())
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

