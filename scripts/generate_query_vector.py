import argparse
import json

from sentence_transformers import SentenceTransformer


DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a 384-dim SBERT query vector for Kibana Dev Tools."
    )
    parser.add_argument("query", help="Query text to embed.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    model = SentenceTransformer(args.model)
    vector = model.encode(args.query, normalize_embeddings=True).tolist()
    print(json.dumps([round(float(value), 6) for value in vector]))


if __name__ == "__main__":
    main()
