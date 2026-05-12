-- Act 1.1 - Synonym failure: expanded phrase works.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%<expanded phrase>%'
   OR description ILIKE '%<expanded phrase>%'
ORDER BY product_id
LIMIT 5;

-- Act 1.1 - Synonym failure: synonym phrase does not equal expanded phrase in PostgreSQL.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%<synonym phrase>%'
   OR description ILIKE '%<synonym phrase>%'
ORDER BY product_id
LIMIT 5;

-- Act 1.2 - Semantic failure.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%<intent query>%'
   OR description ILIKE '%<intent query>%'
ORDER BY product_id
LIMIT 5;

-- Act 1.3 - Ranking failure: stable DB order, not relevance/business quality.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%<ranking query>%'
   OR description ILIKE '%<ranking query>%'
ORDER BY product_id
LIMIT 5;

-- Act 1.4 - Typo failure.
SELECT product_id, title, brand, price, rating, review_count
FROM products
WHERE title ILIKE '%<typo query>%'
   OR description ILIKE '%<typo query>%'
ORDER BY product_id
LIMIT 5;
