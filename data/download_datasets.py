from __future__ import annotations

import argparse
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


DATASETS = {
    "metadata": {
        "url": "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Electronics.jsonl.gz",
        "filename": "meta_Electronics.jsonl.gz",
        "description": "Amazon Electronics product metadata used by the ingest notebook",
    },
    "reviews": {
        "url": "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Electronics.jsonl.gz",
        "filename": "Electronics.jsonl.gz",
        "description": "Amazon Electronics review events, reserved for future personalization work",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Amazon Electronics datasets into data/raw."
    )
    parser.add_argument(
        "--reviews",
        action="store_true",
        help="Also download Electronics.jsonl.gz review events.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download all known datasets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even when they already exist.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "raw",
        help="Target directory for downloaded files. Defaults to data/raw.",
    )
    return parser.parse_args()


def selected_datasets(include_reviews: bool, include_all: bool) -> list[str]:
    if include_all:
        return list(DATASETS)
    names = ["metadata"]
    if include_reviews:
        names.append("reviews")
    return names


def download_file(url: str, target: Path) -> None:
    partial = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {url}")
    print(f"       to {target}")
    if partial.exists():
        partial.unlink()

    with urlopen(url, timeout=60) as response, partial.open("wb") as output:
        total = response.headers.get("Content-Length")
        total_bytes = int(total) if total and total.isdigit() else None
        downloaded = 0
        next_report = 0

        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break

            output.write(chunk)
            downloaded += len(chunk)
            if total_bytes:
                percent = int(downloaded * 100 / total_bytes)
                if percent >= next_report:
                    print(f"  {percent:3d}% ({downloaded / 1024 / 1024:.1f} MiB)")
                    next_report += 10

    partial.replace(target)
    print(f"Done: {target}")


def main() -> int:
    args = parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    for name in selected_datasets(args.reviews, args.all):
        dataset = DATASETS[name]
        target = args.raw_dir / dataset["filename"]

        print(f"\n{name}: {dataset['description']}")
        if target.exists() and not args.force:
            print(f"Skip existing file: {target}")
            continue

        try:
            download_file(dataset["url"], target)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"Failed to download {name}: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
