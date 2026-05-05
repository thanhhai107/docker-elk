-- Act 1.1 - Synonym failure: exact phrase works.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%noise cancelling headphones%'
   OR description ILIKE '%noise cancelling headphones%'
ORDER BY product_id
LIMIT 5;

-- Act 1.1 - Synonym failure: ANC does not equal noise cancelling in PostgreSQL.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%ANC headphones%'
   OR description ILIKE '%ANC headphones%'
ORDER BY product_id
LIMIT 5;

-- Act 1.2 - Semantic failure.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%headphones for working out%'
   OR description ILIKE '%headphones for working out%'
ORDER BY product_id
LIMIT 5;

-- Act 1.3 - Ranking failure: stable DB order, not relevance/business quality.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%bluetooth speaker%'
   OR description ILIKE '%bluetooth speaker%'
ORDER BY product_id
LIMIT 5;

-- Act 1.4 - Typo failure.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%samsug galxy s24%'
   OR description ILIKE '%samsug galxy s24%'
ORDER BY product_id
LIMIT 5;
