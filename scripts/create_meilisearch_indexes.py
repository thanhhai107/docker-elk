from __future__ import annotations

import meilisearch

from backend.config import settings


def create_indexes() -> None:
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    index = client.index("products")
    index.update_filterable_attributes(["brand", "category", "price", "rating"])
    index.update_sortable_attributes(["price", "rating", "review_count"])
    index.update_searchable_attributes(["title", "brand", "category", "description", "review_text"])
    index.update_displayed_attributes(["*"])


if __name__ == "__main__":
    create_indexes()
