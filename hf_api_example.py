import os
from huggingface_hub import InferenceClient


def main():
    # Check multiple possible environment variable names for HuggingFace API key/token
    api_key = (
        os.environ.get("HF_API_KEY") or
        os.environ.get("HUGGINGFACE_API_KEY") or
        os.environ.get("HUGGINGFACE_TOKEN") or
        os.environ.get("HF_TOKEN") or
        os.environ.get("HUGGINGFACE_HUB_TOKEN")
    )
    
    if not api_key:
        raise RuntimeError(
            "HuggingFace API key/token not found. Please set one of these environment variables:\n"
            "  - HF_API_KEY\n"
            "  - HUGGINGFACE_API_KEY\n"
            "  - HUGGINGFACE_TOKEN\n"
            "  - HF_TOKEN\n"
            "  - HUGGINGFACE_HUB_TOKEN"
        )
    
    # Use 'token' parameter (standard for HuggingFace) instead of 'api_key'
    client = InferenceClient(
        provider="hf-inference",
        token=api_key,
    )

    result = client.token_classification(
        "My name is Sarah Jessica Parker but you can call me Jessica",
        model="dslim/bert-base-NER",
    )

    print(result)

if __name__ == "__main__":
    main()