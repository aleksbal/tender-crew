"""
Filter classes for excluding false positives from PII detection.

This module contains filter classes that help identify and exclude
false positives such as city names, technology names, and addresses
that might be misclassified as PERSON or LOCATION entities.
"""

import re
from typing import Set, List

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
    # Comprehensive list merged from multiple sources (588 unique technologies)
    TECHNOLOGY_NAMES = {
        "activemq", "ada", "aiohttp", "airflow", "aix", "akka", "alembic",
        "alibaba cloud", "alibaba-cloud", "alibabacloud", "almalinux", "alpine", "alpinejs",
        "angular", "ansible", "ant", "ant design", "ant-design", "antdesign", "anthropic",
        "apache", "apparmor", "appdynamics", "arangodb", "arch", "argocd", "artemis",
        "asana", "asp.net", "asp.net core", "asp.net-core", "asp.netcore", "athena", "awk",
        "aws", "aws api gateway", "aws athena", "aws aurora", "aws cloudfront", "aws cloudwatch",
        "aws dynamodb", "aws ec2", "aws ecs", "aws eks", "aws glue", "aws iam", "aws kms",
        "aws lambda", "aws rds", "aws route53", "aws s3", "aws sns", "aws sqs", "aws step functions",
        "aws-api-gateway", "aws-athena", "aws-aurora", "aws-cloudfront", "aws-cloudwatch",
        "aws-dynamodb", "aws-ec2", "aws-ecs", "aws-eks", "aws-glue", "aws-iam", "aws-kms",
        "aws-lambda", "aws-rds", "aws-route53", "aws-s3", "aws-sns", "aws-sqs", "aws-step-functions",
        "awsapigateway", "awsathena", "awsaurora", "awscloudfront", "awscloudwatch", "awsdynamodb",
        "awsec2", "awsecs", "awseks", "awsglue", "awsiam", "awskms", "awslambda", "awsrds",
        "awsroute53", "awss3", "awssns", "awssqs", "awsstepfunctions", "azure", "azure devops",
        "azure pipelines", "azure-devops", "azuredevops", "backbone", "bamboo", "bash", "bazel",
        "bdd", "beam", "bigquery", "bigtable", "bitbucket", "black", "blazor", "bootstrap",
        "browserify", "buck", "bulma", "c", "c#", "c++", "caddy", "cakephp", "capacitor", "cargo",
        "cassandra", "catboost", "cd", "cdk", "centos", "cert-manager", "chakra ui", "chakra-ui",
        "chakraui", "chef", "ci", "ci/cd", "circleci", "clang-format", "clickhouse", "clickup",
        "clion", "cloud stream", "cloudflare", "cloudformation", "cmake", "cobol", "cockroachdb",
        "codacy", "codeclimate", "composer", "confluence", "consul", "containerd", "cordova",
        "cosmosdb", "couchbase", "couchdb", "coveralls", "crew", "crew ai", "cron", "crystal",
        "css", "cypress", "dagster", "dart", "dask", "datadog", "datagrip", "db2", "ddd", "debian",
        "derby", "digitalocean", "discord", "django", "dlib", "docker", "docker compose",
        "dropwizard", "dynamics 365", "dynamics-365", "dynamics365", "dynamodb", "dynatrace",
        "eclipse", "eda", "elastic", "elasticsearch", "elixir", "elk", "emacs", "ember",
        "entity framework", "entity-framework", "entityframework", "erlang", "esb", "eslint", "esx",
        "etl", "event sourcing", "event-sourcing", "eventsourcing", "exoscale", "express", "falcon",
        "fastapi", "fedora", "firestore", "fish", "flake8", "flask", "flink", "flutter", "fluxcd",
        "flyway", "fortran", "foundation", "freebsd", "gatsby", "gcp", "gensim", "gentoo", "git",
        "github", "github actions", "github-actions", "githubactions", "gitlab", "gitlab ci",
        "gitlab-ci", "gitlabci", "glassfish", "gnu make", "go", "gofmt", "golang", "google cloud",
        "gradle", "grafana", "graphql", "graylog", "greenplum", "groovy", "grpc", "grub", "grunt",
        "gulp", "h2", "hack", "hanami", "hapi", "haproxy", "haskell", "hbase", "helm", "heroku",
        "hetzner", "hibernate", "hsql", "html", "htop", "huggingface", "husky", "iam", "ibm cloud",
        "ibm-cloud", "ibmcloud", "ifconfig", "iis", "influxdb", "insomnia", "instana", "intellij",
        "intellij idea", "intellij-idea", "intellijidea", "ionic", "iptables", "istio", "jasmine",
        "java", "javascript", "jboss", "jenkins", "jest", "jetty", "jira", "journalctl", "jpa",
        "jquery", "jsf", "jsp", "julia", "junit", "jwt", "kafka", "karma", "keras", "keycloak",
        "kibana", "koa", "kornshell", "kotlin", "kubeflow", "kubernetes", "kustomize", "kvm",
        "langchain", "laravel", "ldap", "less", "leveldb", "lightgbm", "lightsail", "linkerd",
        "linode", "lint-staged", "linux", "liquibase", "lit", "llama", "lodash", "logstash",
        "lombok", "ltrace", "lua", "lvm", "macos", "magento", "make", "mapstruct", "mariadb",
        "material ui", "material-ui", "materialui", "matlab", "mattermost", "maui", "maven",
        "mercurial", "meteor", "micronaut", "microservices", "mlflow", "mocha", "mockito",
        "monday", "mongodb", "monolith", "mypy", "mysql", "nagios", "nano", "nats", "neo4j",
        "nestjs", "netbeans", "netlify", "new relic", "new-relic", "newrelic", "nextjs", "nftables",
        "nginx", "nim", "nltk", "nmcli", "nock", "node", "nodejs", "nomad", "notion", "npm", "numpy",
        "nuxt", "oauth", "oauth2", "objective-c", "ocaml", "odoo", "olap", "ollama", "oltp",
        "openai", "openapi", "openbsd", "opencv", "openshift", "openstack", "opentelemetry",
        "openvz", "oracle", "oracle cloud", "oracle ebs", "oracle-cloud", "oracle-ebs",
        "oraclecloud", "oracleebs", "ovh", "packer", "pandas", "pants", "parcel", "peoplesoft",
        "perforce", "perl", "php", "pip", "pipenv", "piral", "playwright", "plsql", "pnpm",
        "podman", "poetry", "pony", "postgres", "postgresql", "postman", "powershell", "prefect",
        "prettier", "prometheus", "protobuf", "protocol buffers", "proxmox", "pulsar", "pulumi",
        "puppet", "pycharm", "pylint", "pyramid", "pytest", "python", "pytorch", "qemu", "quarkus",
        "querydsl", "r", "rabbitmq", "rails", "rancher", "ray", "react", "react native",
        "react-native", "reactnative", "red", "redhat", "redis", "redshift", "remix", "rest",
        "rider", "rocksdb", "rockylinux", "rollbar", "rollup", "rspec", "rsync", "rubocop", "ruby",
        "rust", "rxjs", "sails", "salesforce", "saltstack", "saml", "sap", "sass", "scala",
        "scaleway", "scikit-learn", "scipy", "scratch", "screen", "scss", "sed", "selenium",
        "selinux", "sentry", "serverless", "servicenow", "shopify", "sinatra", "sinon", "slack",
        "smalltalk", "snowflake", "soa", "soap", "solaris", "solidity", "solidjs", "sonarqube",
        "spacy", "spark", "spinnaker", "splunk", "spock", "spring", "spring batch", "spring boot",
        "spring cloud", "spring cloud stream", "spring security", "spring-batch", "spring-boot",
        "spring-cloud", "spring-security", "springbatch", "springboot", "springcloud",
        "springsecurity", "sql", "sqlite", "sso", "statsmodels", "stencil", "strace", "stylelint",
        "sublime", "suse", "svelte", "sveltekit", "svn", "swagger", "swift", "symfony", "systemd",
        "tailwind", "tcl", "tcpdump", "tdd", "teamcity", "teams", "tensorflow", "teradata",
        "terraform", "testcontainers", "testng", "thymeleaf", "timescaledb", "tmux", "tomcat",
        "tornado", "traefik", "transformers", "travis", "trello", "typescript", "typo3", "ubuntu", "udev",
        "underscore", "vaadin", "vagrant", "vault", "vercel", "verilog", "vertica", "vertx",
        "vhdl", "vim", "virtualbox", "vite", "vmware", "vscode", "vue", "vultr", "webpack",
        "webstorm", "werf", "wildfly", "windows", "winforms", "wiremock", "wireshark", "workday",
        "wpf", "xamarin", "xen", "xgboost", "yarn", "yii", "yugabytedb", "zabbix", "zend",
        "zeromq", "zig", "zoom", "zsh",
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
    def _is_substring_of_technology(cls, text: str) -> List[str]:
        """
        Check if text is a substring of any technology name.
        Returns list of technology names that contain this text as a substring.
        
        The key insight: Presidio uses direct string replacement (not regex with word boundaries),
        so if "Java" is detected at positions 10-14 in "JavaScript" (positions 10-20),
        it would replace characters 10-14 with "<PERSON1>", resulting in "<PERSON1>Script".
        We need to prevent this by checking if the detected text is part of a technology name.
        
        Examples:
        - "Ty" is prefix of "Typo3" → returns ["typo3"] (would break: "<PERSON1>po3")
        - "Java" is prefix of "JavaScript" → returns ["javascript"] (would break: "<PERSON1>Script")
        - "React" is prefix of "React Native" → returns ["react native"] (would break: "<PERSON1> Native")
        - "Node" is prefix of "NodeJS" → returns ["nodejs"] (would break: "<PERSON1>JS")
        - "Type" is prefix of "TypeScript" → returns ["typescript"] (would break: "<PERSON1>Script")
        """
        text_lower = safe_lower(text.strip())
        if len(text_lower) < 2:  # Too short to be meaningful
            return []
        
        exclude_set = cls._get_exclude_set()
        matching_techs = []
        
        for tech_name in exclude_set:
            # Check if text is a substring of the technology name
            # Since Presidio uses direct string replacement, ANY substring match is dangerous
            if text_lower in tech_name and len(tech_name) > len(text_lower):
                # Only consider if it's a meaningful substring (at least 2 chars)
                if len(text_lower) >= 2:
                    matching_techs.append(tech_name)
        
        return matching_techs
    
    @classmethod
    def _is_prefix_of_technology(cls, text: str) -> bool:
        """
        Check if text is a prefix of any technology name.
        This prevents partial matches like "Ty" being misclassified when "Typo3" is the actual technology.
        """
        text_lower = safe_lower(text.strip())
        if len(text_lower) < 2:  # Too short to be a meaningful prefix
            return False
        
        exclude_set = cls._get_exclude_set()
        # Check if any technology name starts with this text (with word boundary)
        # This catches cases like "Ty" -> "Typo3", "Re" -> "React", etc.
        for tech_name in exclude_set:
            if tech_name.startswith(text_lower) and len(tech_name) > len(text_lower):
                # Additional check: ensure it's a meaningful prefix (not just coincidence)
                # e.g., "Ty" is a prefix of "Typo3" (good), but "a" is prefix of many (bad)
                if len(text_lower) >= 2:  # At least 2 characters
                    return True
        return False
    
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
    def should_exclude(cls, text: str, context: str = None) -> bool:
        """
        Determine if text should be excluded because it's a technology name or false positive.
        
        This method prevents obfuscation of any text that could be part of a technology name,
        preventing partial obfuscation like "Java" -> "<PERSON1>Script" when "JavaScript" is present.
        
        Args:
            text: The text to check
            context: Optional surrounding context to check for full technology names
        """
        # First check standard technology name matching
        if cls.is_technology_name(text):
            return True
        
        text_lower = safe_lower(text.strip())
        
        # Check if text is a substring of any technology name
        # This is the critical check: if "Java" is a substring of "JavaScript", we must exclude it
        matching_techs = cls._is_substring_of_technology(text)
        
        if matching_techs:
            # If context is provided, verify the full technology name appears in context
            if context:
                context_lower = safe_lower(context)
                found_in_context = False
                
                # Check if any of the matching technology names appear in the context
                for tech_name in matching_techs:
                    # Use word boundary to find the full technology name
                    # This prevents false matches like "java" matching "javascript" when we want exact "javascript"
                    pattern = r'\b' + re.escape(tech_name) + r'\b'
                    if re.search(pattern, context_lower):
                        # The full technology name appears in context, so exclude the substring
                        found_in_context = True
                        break
                
                # Also check without word boundaries for compound names like "NodeJS" or "React Native"
                if not found_in_context:
                    for tech_name in matching_techs:
                        if tech_name in context_lower:
                            # The full technology name appears (even if part of a compound), exclude the substring
                            found_in_context = True
                            break
                
                # Only exclude if we found a matching tech name in context
                if found_in_context:
                    return True
            else:
                # Without context, be more conservative: only exclude if substring is a PREFIX
                # This prevents false positives (e.g., "Ty" in "stylelint" shouldn't exclude "Ty")
                # Only exclude if the substring starts the technology name
                for tech_name in matching_techs:
                    if tech_name.startswith(text_lower) and len(tech_name) > len(text_lower):
                        # It's a prefix, and tech name is longer - exclude to prevent breaking
                        if len(text_lower) >= 2 and len(tech_name) >= len(text_lower) + 2:
                            return True
        
        # Also check if text is a prefix of a technology name (for backward compatibility)
        if cls._is_prefix_of_technology(text):
            exclude_set = cls._get_exclude_set()
            
            # If context is provided, verify the full technology name appears nearby
            if context:
                context_lower = safe_lower(context)
                
                # Look for technology names that start with this prefix in the context
                for tech_name in exclude_set:
                    if tech_name.startswith(text_lower) and len(tech_name) > len(text_lower):
                        # Check if the full technology name appears in context
                        # Use word boundary to avoid partial matches
                        pattern = r'\b' + re.escape(tech_name) + r'\b'
                        if re.search(pattern, context_lower):
                            return True
            else:
                # Without context, be conservative but smart:
                # Only exclude if the prefix is at least 2 chars and the tech name is significantly longer
                for tech_name in exclude_set:
                    if tech_name.startswith(text_lower) and len(tech_name) > len(text_lower):
                        # If prefix is 2+ chars and tech name is at least 3 chars longer, exclude
                        if len(text_lower) >= 2 and len(tech_name) >= len(text_lower) + 2:
                            return True
        
        return False

