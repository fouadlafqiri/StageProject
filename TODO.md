# MonoProductStage Angular Migration TODO

## Plan baseline (high level)
- Recreate the UI from scratch as Angular components/templates.
- Remove dependency on the static HTML/JS pages.
- Port the landing generation logic from `front-end/script.js` into `front-end/product.service.ts`.

## Steps
1. Create Angular template files:
   - `front-end/home.component.html`
   - `front-end/home.component.css`
   - `front-end/results.component.html`
   - `front-end/results.component.css`
2. Implement the missing UI wiring in:
   - `front-end/home.component.ts` (login/register UI state + generate navigation)
   - `front-end/results.component.ts` (template selection + iframe preview + download)
3. Replace placeholder in `front-end/product.service.ts`:
   - Implement `buildLandingHtml(product, templateKey)` using the full logic from `front-end/script.js`.
4. Ensure Angular routing works (Home at `/`, Results at `/resultats`):
   - Verify/adjust `front-end/app.module.ts` if needed.
5. Validate by running the Angular app and checking:
   - scrape -> navigation -> preview update -> download.
6. Optionally delete/ignore legacy static files (`front-end/index.html`, `front-end/preview.html`, `front-end/resultats.html`, `front-end/script.js`) after success.

