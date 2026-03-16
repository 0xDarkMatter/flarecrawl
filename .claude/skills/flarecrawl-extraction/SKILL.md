---
name: flarecrawl-extraction
description: "Flarecrawl AI-powered data extraction workflows. Triggers: extract data, structured extraction, AI scraping, schema extraction, Workers AI"
version: 1.0.0
category: domain
tool: flarecrawl
requires:
  bins: ["flarecrawl"]
  skills: ["flarecrawl-ops"]
allowed-tools: "Read Bash Grep"
---

# Flarecrawl Extraction

AI-powered structured data extraction from web pages using Cloudflare Workers AI. Extract structured JSON from any page using natural language prompts and optional JSON schemas.

## Key Commands

### Basic extraction

```bash
flarecrawl extract "Get all product names and prices" --urls https://shop.example.com --json
```

### With JSON schema

```bash
flarecrawl extract "Extract article metadata" --urls https://blog.example.com \
  --schema '{"type":"json_schema","schema":{"type":"object","properties":{"title":{"type":"string"},"date":{"type":"string"},"author":{"type":"string"}}}}'
```

### Schema from file

```bash
flarecrawl extract "Extract data" --urls https://example.com --schema-file schema.json
```

### Multiple URLs (sequential)

```bash
flarecrawl extract "Get page title" --urls https://a.com,https://b.com --json
```

### Batch extraction (parallel)

```bash
flarecrawl extract "Get page title and description" --batch urls.txt --workers 5
# Output: NDJSON with {index, status, data/error}
```

## Workflows

### Schema-driven extraction

1. Define a JSON schema describing the expected output shape
2. Write a clear prompt describing what to extract
3. Run extraction with `--schema` or `--schema-file`

```bash
# schema.json
cat > schema.json << 'EOF'
{
  "type": "json_schema",
  "schema": {
    "type": "object",
    "properties": {
      "products": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "price": {"type": "number"},
            "currency": {"type": "string"}
          }
        }
      }
    }
  }
}
EOF

flarecrawl extract "Extract all products with their prices" \
  --urls https://shop.example.com --schema-file schema.json --json
```

### Batch extraction pipeline

```bash
# 1. Discover pages
flarecrawl map https://shop.example.com --json | jq -r '.data[]' | grep '/product/' > products.txt

# 2. Batch extract with schema
flarecrawl extract "Get product name, price, and description" \
  --batch products.txt --workers 5 --schema-file schema.json

# 3. Filter successful results
flarecrawl extract "Get product name and price" --batch products.txt \
  | jq 'select(.status == "ok") | .data'
```

### Extract vs scrape --format json

| Feature | `extract` | `scrape --format json` |
|---------|-----------|----------------------|
| Custom prompt | Yes | No (generic "extract main content") |
| JSON schema | Yes (`--schema`) | No |
| Batch mode | Yes (`--batch`) | Yes (`--batch`) |
| Use case | Structured data | Quick content extraction |

Use `extract` when you need specific fields. Use `scrape --format json` for generic content.

## Domain-Specific Gotchas

- **Workers AI models** have context limits — very large pages may truncate
- **Prompt quality matters** — be specific about what fields you want
- **Schema validation** is best-effort — Workers AI may not perfectly match complex schemas
- **No streaming** — extraction waits for the full AI response
- **Cost: $0** — Workers AI extraction is included in Browser Rendering pricing
- **Batch output is NDJSON**, not `{data, meta}` envelope — parse line by line
- **Combine `--urls` and `--batch`** — batch URLs supplement `--urls` comma list
- **`--body` bypass** — for advanced Workers AI options not exposed as flags:
  ```bash
  flarecrawl extract --body '{"url":"https://example.com","prompt":"Extract data","response_format":{"type":"json_schema","schema":{...}}}' --json
  ```
