CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS products (
    product_id text PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    category text NOT NULL DEFAULT 'Electronics',
    brand text NOT NULL DEFAULT 'Unknown',
    price double precision,
    rating double precision NOT NULL DEFAULT 0,
    review_count integer NOT NULL DEFAULT 0,
    avg_review_rating double precision NOT NULL DEFAULT 0,
    loaded_review_count integer NOT NULL DEFAULT 0,
    helpful_votes integer NOT NULL DEFAULT 0,
    review_text text NOT NULL DEFAULT '',
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(brand, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(category, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(description, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(review_text, '')), 'D')
    ) STORED
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id text PRIMARY KEY,
    product_id text NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    user_id text NOT NULL DEFAULT 'anonymous',
    rating double precision NOT NULL DEFAULT 0,
    title text NOT NULL DEFAULT '',
    text text NOT NULL DEFAULT '',
    helpful_vote integer NOT NULL DEFAULT 0,
    timestamp bigint
);

CREATE INDEX IF NOT EXISTS products_search_vector_idx ON products USING gin(search_vector);
CREATE INDEX IF NOT EXISTS products_title_trgm_idx ON products USING gin(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS products_description_trgm_idx ON products USING gin(description gin_trgm_ops);
CREATE INDEX IF NOT EXISTS products_brand_idx ON products(brand);
CREATE INDEX IF NOT EXISTS products_category_idx ON products(category);
CREATE INDEX IF NOT EXISTS products_price_idx ON products(price);
CREATE INDEX IF NOT EXISTS products_rating_idx ON products(rating);
CREATE INDEX IF NOT EXISTS reviews_product_id_idx ON reviews(product_id);
