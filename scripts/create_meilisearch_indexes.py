from __future__ import annotations

import meilisearch

from backend.config import settings


def create_indexes() -> None:
    client = meilisearch.Client(settings.meili_url, settings.meili_master_key)
    products = client.index("amazon_electronics_products")
    wait(client, products.update_filterable_attributes(["brand", "category", "price", "rating", "average_rating"]))
    wait(client, products.update_sortable_attributes(["price", "rating", "review_count", "average_rating", "rating_number"]))
    wait(client, products.update_searchable_attributes(["title", "brand", "category", "features", "description", "review_text"]))
    wait(client, products.update_displayed_attributes(["*"]))

    reviews = client.index("amazon_electronics_reviews")
    wait(client, reviews.update_filterable_attributes(["product_id", "rating", "verified_purchase"]))
    wait(client, reviews.update_sortable_attributes(["rating", "helpful_vote"]))
    wait(client, reviews.update_searchable_attributes(["title", "text"]))
    wait(client, reviews.update_displayed_attributes(["*"]))


def task_uid(task) -> int:
    if hasattr(task, "task_uid"):
        return task.task_uid
    return int(task["taskUid"])


def wait(client: meilisearch.Client, task) -> None:
    client.wait_for_task(task_uid(task), timeout_in_ms=120000)


if __name__ == "__main__":
    create_indexes()
