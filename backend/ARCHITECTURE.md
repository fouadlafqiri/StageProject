# Backend Architecture

The FastAPI app exposes stable API routes from `main.py` and keeps the production boundaries below:

- `database`: MySQL schema initialization, user lookup, and product persistence.
- `scraping`: URL loading, structured data extraction, platform detection, and image filtering.
- `ai`: image analysis, image-product verification, and marketing/SEO enrichment.
- `search`: SerpAPI/Google discovery for product URLs and product images.
- `presentation`: normalized product payloads consumed by Angular landing-page generation.

Current API endpoints:

- `POST /api/scrape` generates a product from a product URL.
- `POST /api/validate-image` checks whether an uploaded file is a usable product image.
- `POST /api/search-by-image` detects a product from an uploaded image, searches candidates, verifies matches, and generates the product payload.
- `POST /api/product/search-by-image-url` repeats the image workflow from an existing image URL.
- `POST /api/product/{product_id}/images/more` searches additional relevant product images.
- `GET /api/products` lists saved products.
- `POST /api/auth/register` and `POST /api/auth/login` manage local accounts.

Environment variables:

- `ANTHROPIC_API_KEY` enables vision verification and enrichment.
- `SERPAPI_KEY` enables Google Shopping/Search/Image discovery.
- `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` configure MySQL.
