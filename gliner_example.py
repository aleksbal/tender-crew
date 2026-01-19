from typing import Any

from gliner2 import GLiNER2


def extract_entities(text: str) -> dict[str, Any]:

    extractor = GLiNER2.from_pretrained("fastino/gliner2-multi-v1")

    result = extractor.extract_entities(text, ["account number", "person", "address", "time frame", "link", "tel", "location", "mail", "technology"])

    return result

def main():

    # Extract entities
    text = "Apple CEO Tim Cook announced iPhone 15 in Cupertino yesterday. Aleksandar Balaban ist ein Mensch"
    sample = """
        Lebenslauf

        Aleksandar Balaban
        Geboren 01.01.1987
        2. Etage Hobrechtstr. 23 12047
        12047 Berlin     
        Office: 06221 / 123456
        Mobile: +49 171 2345678

        Online Producer/Webdeveloper
        selbstständig freiberuflich

        Projects:

        2/2025 - 12/2025
        My last Java project
        Entwicklung von Software für WWW    
        used tech: Java, Python, JavaScript, Oracle, MySQL, Kafka, Anthropic 
        Kontonummer: DE10023234433221234
         
        2/2023 - 2/2025
        My first Java project für eine Hochschule
        Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL
        feb 1990 - dez 2022
        My first Java project
        Verwendung von Prometheus für tracking 
        Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL, Gradle
        2/1997 - 12/1998
        My first Java project
        Verwendung von Prometheus für tracking 
        Used tech: Python, Jenkins, Crew AI, JavaScript, Oracle, MySQL, Gradle
        
        Wenden Sie sich bitte an Klaus Kirchner oder Marta Kos für weitere Info.
    """
    result = extract_entities(sample)

    print(result)
    # Output: {'entities': {'company': ['Apple'], 'person': ['Tim Cook'], 'product': ['iPhone 15'], 'location': ['Cupertino']}}
if __name__ == "__main__":
    main()