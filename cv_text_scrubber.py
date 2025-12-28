from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine


def anonymize_cv(text):
    # Initialize engines
    analyzer = AnalyzerEngine()
    anonymizer = AnonymizerEngine()

    # Define PII entities to detect (Names, Addresses, Phone Numbers, Emails)
    # 'LOCATION' typically catches street addresses and city info
    target_entities = ["PERSON", "LOCATION", "PHONE_NUMBER", "EMAIL_ADDRESS"]

    # 1. Analyze: Find where the PII is in the text
    analyzer_results = analyzer.analyze(
        text=text,
        entities=target_entities,
        language='en'
    )

    # 2. Anonymize: Replace findings with generic tags like <PERSON>
    anonymized_result = anonymizer.anonymize(
        text=text,
        analyzer_results=analyzer_results
    )

    return anonymized_result.text


def main():
    # Example CV content as plain text
    sample_cv = """
    Aleksandar Balaban
    Senior Data Engineer

    CONTACT INFO
    Address: Hobrechstrasse 29 12047 berlin
    Phone: (512) 555-0123
    Email: j.smith.tech@example.com
    LinkedIn: linkedin.com

    SUMMARY
    Highly skilled engineer with 8 years of experience in cloud infrastructure.
    Previously worked at TechGlobal Inc. managing large-scale data pipelines.
    """

    print("--- ORIGINAL TEXT ---")
    print(sample_cv)

    print("\n--- ANONYMIZED TEXT ---")
    clean_text = anonymize_cv(sample_cv)
    print(clean_text)


if __name__ == "__main__":
    main()
