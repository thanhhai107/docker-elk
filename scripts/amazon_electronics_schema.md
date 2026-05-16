# Amazon Electronics Dataset Schema

This document contains only the dataset schema fields shown for the Amazon Electronics dataset.

## User Reviews Schema

| Field | Type | Description |
|---|---|---|
| `rating` | `float` | Rating of the product, from 1.0 to 5.0. |
| `title` | `str` | Title of the user review. |
| `text` | `str` | Text body of the user review. |
| `images` | `list` | Images that users post after receiving the product. Each image may contain different sizes such as small, medium, and large image URLs. |
| `asin` | `str` | ID of the product. |
| `parent_asin` | `str` | Parent ID of the product. Products with different colors, styles, or sizes usually belong to the same parent ID. In previous Amazon datasets, `asin` may correspond to the parent ID. Use `parent_asin` to find product metadata. |
| `user_id` | `str` | ID of the reviewer. |
| `timestamp` | `int` | Time of the review, represented as Unix time. |
| `verified_purchase` | `bool` | Whether the review is from a verified purchase. |
| `helpful_vote` | `int` | Number of helpful votes for the review. |

## Item Metadata Schema

| Field | Type | Description |
|---|---|---|
| `main_category` | `str` | Main category or domain of the product. |
| `title` | `str` | Name of the product. |
| `average_rating` | `float` | Rating of the product shown on the product page. |
| `rating_number` | `int` | Number of ratings for the product. |
| `features` | `list` | Bullet-point features of the product. |
| `description` | `list` | Description of the product. |
| `price` | `float` | Price in US dollars at the time of crawling. |
| `images` | `list` | Images of the product. Each image has different sizes such as thumbnail, large, and high-resolution. The `variant` field shows the image position. |
| `videos` | `list` | Videos of the product, including title and URL. |
| `store` | `str` | Store name of the product. |
| `categories` | `list` | Hierarchical categories of the product. |
| `details` | `dict` | Product details, including materials, brand, sizes, and other attributes. |
| `parent_asin` | `str` | Parent ID of the product. |
| `bought_together` | `list` | Recommended product bundles from the website. |