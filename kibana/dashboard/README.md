# Kibana Dashboard Setup

Use this after ingest and after at least one zero-result log exists in
`shopx_logs`.

## Data Views

Create these Kibana data views:

- `shopx_products`
- `shopx_logs`
- `demo_scale`

Set the time field for `shopx_logs` to `timestamp`.

## Zero-Result Analytics Dashboard

Create a Lens visualization:

- Data view: `shopx_logs`
- Filter: `is_zero_result: true`
- Chart: horizontal bar
- Breakdown: top values of `query.keyword`
- Metric: count of records

Suggested title:

```text
Top Zero-Result Queries
```

Add a second metric:

- Data view: `shopx_logs`
- Filter: `is_zero_result: true`
- Metric: count of records
- Title: `Zero-Result Searches`

## Product Search Overview

Optional Lens panels from `shopx_products`:

- Top brands by document count: top values of `brand`
- Average price by category: `category` terms + average of `price`
- Average rating by brand: `brand` terms + average of `rating`
