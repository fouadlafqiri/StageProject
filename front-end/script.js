const form = document.getElementById('productForm');
const productName = document.getElementById('productName');
const productUrl = document.getElementById('productUrl');
const templateSelect = document.getElementById('templateSelect');
const scrapeButton = document.getElementById('scrapeButton');
const downloadButton = document.getElementById('downloadButton');
const statusMessage = document.getElementById('statusMessage');
const sitePreview = document.getElementById('sitePreview');
const productDetails = document.getElementById('productDetails');
const currentTemplate = document.getElementById('currentTemplate');
const openProductLink = document.getElementById('openProductLink');
const authPanel = document.getElementById('authPanel');
const generatorPanel = document.getElementById('generatorPanel');
const sessionBar = document.getElementById('sessionBar');
const sessionName = document.getElementById('sessionName');
const logoutButton = document.getElementById('logoutButton');
const loginForm = document.getElementById('loginForm');
const registerForm = document.getElementById('registerForm');
const authStatus = document.getElementById('authStatus');
const authTabs = document.querySelectorAll('[data-auth-tab]');
const authForms = document.querySelectorAll('[data-auth-form]');
const startButton = document.getElementById('startButton');
const API_BASE_URL = location.port === '8000' ? '' : 'http://127.0.0.1:8000';
const STORAGE_KEY = 'monoProductGeneratedSite';
const AUTH_KEY = 'monoProductUser';

const fallbackProduct = {
  name: 'Produit exemple',
  tagline: 'Une page mono-produit générée automatiquement à partir d\'une URL.',
  description: 'Le générateur récupère les données disponibles sur la page produit puis construit un site complet avec image, prix, bénéfices et appel à l\'action.',
  price: 'Prix à récupérer',
  image: 'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80',
  images: ['https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80'],
  features: ['Scraping des métadonnées', 'Template personnalisable', 'Page HTML téléchargeable', 'Lien vers le produit original'],
  cta: 'Voir le produit',
  url: '#',
  availability: 'Disponibilité à vérifier',
  rating: '',
  sections: {
    benefitTitle: 'Pourquoi choisir ce produit ?',
    proofTitle: 'Informations produit',
    closingTitle: 'Prêt à passer à l\'action ?',
  },
};

let generatedProduct = { ...fallbackProduct };
let generatedHtml = '';

const templates = {
  minimal: {
    label: 'Minimal premium',
    accent: '#0f766e',
    background: '#f7faf9',
    ink: '#10201d',
    card: '#ffffff',
    font: 'Inter, Arial, sans-serif',
    layout: 'split',
  },
  tech: {
    label: 'Tech moderne',
    accent: '#2563eb',
    background: '#eef4ff',
    ink: '#111827',
    card: '#ffffff',
    font: 'Segoe UI, Arial, sans-serif',
    layout: 'stack',
  },
  boutique: {
    label: 'Boutique élégante',
    accent: '#be123c',
    background: '#fff7f8',
    ink: '#241116',
    card: '#ffffff',
    font: 'Georgia, Times New Roman, serif',
    layout: 'editorial',
  },
  molla: {
    label: 'Index2 Molla',
    accent: '#c96',
    background: '#ffffff',
    ink: '#222222',
    card: '#ffffff',
    font: 'Poppins, Arial, sans-serif',
    layout: 'molla',
  },
};

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function safeUrl(value = '#') {
  const url = String(value || '#').trim();
  if (url.startsWith('//')) return `https:${url}`;
  return url.startsWith('http://') || url.startsWith('https://') ? url : '#';
}

function isValidProductUrl(value = '') {
  if (safeUrl(value) === '#') return false;
  try {
    const parsed = new URL(value);
    return Boolean(parsed.hostname);
  } catch (error) {
    return false;
  }
}

function defaultMarketingFeatures(name = fallbackProduct.name) {
  return [
    `Présentation claire de ${name}`,
    'Visuels et informations clés mis en avant',
    'Page optimisée pour rassurer et convertir',
    "Appel à l'action relié à la page officielle",
  ];
}

function isUsefulFeature(feature = '', productName = '') {
  const text = String(feature || '').trim();
  if (!text) return false;

  const lower = text.toLowerCase();
  const blocked = new Set([
    'apple',
    'store',
    'mac',
    'ipad',
    'iphone',
    'watch',
    'airpods',
    'tv & home',
    'entertainment',
    'accessories',
    'support',
    'shop',
    'learn more',
    'buy',
    'compare',
    'search',
    'bag',
  ]);
  if (blocked.has(lower) || lower === String(productName || '').trim().toLowerCase()) return false;

  const wordCount = (text.match(/\p{L}+/gu) || []).length;
  const hasFactMarker = text.includes(':') || /\d/.test(text);
  return text.length >= 18 && text.length <= 140 && (wordCount >= 4 || hasFactMarker);
}

function normalizeFeatures(features, productName) {
  const cleanFeatures = Array.isArray(features)
    ? features.filter((feature) => isUsefulFeature(feature, productName))
    : [];
  const merged = [...cleanFeatures, ...defaultMarketingFeatures(productName)];
  return [...new Set(merged)].slice(0, 6);
}

function productImageTokens(value = '') {
  return String(value)
    .toLowerCase()
    .match(/[a-z0-9]+/g)
    ?.filter((token) => token.length > 1 && !['the', 'and', 'for', 'avec', 'pour', 'plus', 'new'].includes(token)) || [];
}

function strongProductImageTokens(value = '') {
  const generic = new Set(['ultra', 'pro', 'max', 'mini', 'plus', 'phone', 'smartphone', 'product', 'produit']);
  return productImageTokens(value).filter((token) => !generic.has(token) && (token.length >= 4 || /\d/.test(token)));
}

function imageRelevanceScore(url = '', productName = '', mainImage = '') {
  const lower = String(url).toLowerCase();
  if (/(logo|icon|sprite|placeholder|avatar|favicon|payment|badge|trade|tradein|trade-in|carrier|financ|compare|setup|support|store|banner)/i.test(lower)) {
    return -20;
  }
  if (/\.(svg|ico|gif)(\?|$)/i.test(lower)) return -20;

  let score = url === mainImage ? 8 : 0;
  const tokens = productImageTokens(productName);
  const strongTokens = strongProductImageTokens(productName);
  const compactName = tokens.join('');
  const compactUrl = lower.replace(/[^a-z0-9]+/g, '');
  const competingTerms = ['airpods', 'galaxy', 'ipad', 'iphone', 'macbook', 'odyssey', 'pixel', 'playstation', 'xbox'];
  const expectedTerms = new Set(tokens);

  if (competingTerms.some((term) => !expectedTerms.has(term) && compactUrl.includes(term))) {
    return -30;
  }

  tokens.forEach((token) => {
    if (lower.includes(token)) score += 4;
  });
  strongTokens.forEach((token) => {
    if (compactUrl.includes(token)) score += 5;
  });
  if (compactName && compactUrl.includes(compactName)) score += 6;
  if (/(product|hero|main|gallery|finish|color|packshot)/i.test(lower)) score += 3;
  return score;
}

function normalizeImages(images, productName, mainImage) {
  const uniqueImages = [...new Set([mainImage, ...(Array.isArray(images) ? images : [])])]
    .map(safeUrl)
    .filter((url) => url !== '#');
  const scored = uniqueImages.map((url, index) => ({
    url,
    index,
    score: imageRelevanceScore(url, productName, mainImage),
  }));
  const relevant = scored.filter((item) => item.score > 0);
  const pool = relevant.length ? relevant : scored.filter((item) => item.score > -20);
  pool.sort((a, b) => b.score - a.score || a.index - b.index);
  const selected = pool.map((item) => item.url).slice(0, 5);
  return selected.length ? selected : [fallbackProduct.image];
}

function setStatus(text, type = 'info') {
  if (!statusMessage) return;
  statusMessage.textContent = text;
  statusMessage.className = `info-message ${type}`;
}

function formatApiDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => item?.msg || item?.detail || JSON.stringify(item))
      .filter(Boolean)
      .join(' ');
  }
  if (typeof detail === 'object') {
    return detail.msg || detail.detail || JSON.stringify(detail);
  }
  return String(detail);
}

function getReadableFetchError(error) {
  const message = String(error?.message || '');

  if (message.includes('Unexpected token')) {
    return 'Réponse serveur invalide. Vérifiez que FastAPI est lancé sur http://127.0.0.1:8000.';
  }

  if (
    error instanceof TypeError
    || message.toLowerCase().includes('failed to fetch')
    || message.toLowerCase().includes('networkerror')
  ) {
    return 'Backend inaccessible. Lancez FastAPI avec: python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000';
  }

  return message || 'Impossible de récupérer le produit.';
}

function getCurrentUser() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || 'null');
  } catch (error) {
    localStorage.removeItem(AUTH_KEY);
    return null;
  }
}

function setCurrentUser(user) {
  if (user?.id) {
    localStorage.setItem(AUTH_KEY, JSON.stringify(user));
  } else {
    localStorage.removeItem(AUTH_KEY);
  }
  updateAuthView();
}

function setAuthStatus(text, type = 'info') {
  if (!authStatus) return;
  authStatus.textContent = text;
  authStatus.className = `auth-status ${type}`;
}

function switchAuthTab(tabName) {
  authTabs.forEach((tab) => tab.classList.toggle('active', tab.dataset.authTab === tabName));
  authForms.forEach((formElement) => formElement.classList.toggle('hidden', formElement.dataset.authForm !== tabName));
  setAuthStatus(tabName === 'login' ? 'Connectez-vous pour utiliser le generateur.' : 'Creez un compte pour commencer.', 'info');
}

function updateAuthView() {
  const user = getCurrentUser();
  if (sessionBar) sessionBar.hidden = !user;
  if (sessionName) sessionName.textContent = user ? user.nom || user.email : '';
  if (authPanel) authPanel.hidden = Boolean(user);
  if (generatorPanel) generatorPanel.hidden = !user;
}

function requireAuthenticatedUser() {
  const user = getCurrentUser();
  if (user?.id) return user;
  if (sitePreview) {
    window.location.href = 'index.html';
    return null;
  }
  setAuthStatus('Connexion obligatoire avant de generer un produit.', 'error');
  authPanel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return null;
}

async function submitAuth(endpoint, payload) {
  const response = await fetch(`${API_BASE_URL}/api/auth/${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    body: JSON.stringify(payload),
  });
  const responseText = await response.text();
  let body = null;

  try {
    body = responseText ? JSON.parse(responseText) : null;
  } catch (error) {
    throw new Error('Reponse serveur invalide.');
  }

  if (!response.ok) {
    throw new Error(formatApiDetail(body?.detail) || responseText || 'Authentification impossible.');
  }

  return body.user;
}

function normalizeProduct(data = {}) {
  const productName = data.name || fallbackProduct.name;
  const image = safeUrl(data.image) === '#' ? fallbackProduct.image : safeUrl(data.image);
  const imageList = normalizeImages(data.images, productName, image);

  return {
    ...fallbackProduct,
    ...data,
    sections: { ...fallbackProduct.sections, ...(data.sections || {}) },
    features: normalizeFeatures(data.features, productName),
    url: safeUrl(data.url || productUrl?.value),
    image: imageList[0] || image,
    images: imageList,
  };
}

function isGeneratedProductValid(product = {}) {
  return Boolean(
    String(product.name || '').trim()
    && isValidProductUrl(product.url || '')
    && safeUrl(product.image || '') !== '#'
  );
}

function renderDetails(product) {
  if (!productDetails) return;
  const rows = [
    ['Nom', product.name],
    ['Prix', product.price],
    ['Disponibilité', product.availability],
    ['Note', product.rating || 'Non trouvée'],
    ['Contenu', product.contentSource === 'claude' ? 'Claude API' : 'Génération locale'],
    ['Template', templates[templateSelect?.value || 'minimal'].label],
  ];

  productDetails.innerHTML = rows
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join('');
}

function buildLandingHtml(product, templateKey) {
  if (templateKey === 'molla') {
    return buildMollaLandingHtml(product);
  }

  const template = templates[templateKey] || templates.minimal;
  const seoTitle = product.seo?.title || product.name;
  const seoDescription = product.seo?.description || product.tagline || product.description;
  const features = product.features
    .slice(0, 6)
    .map((feature) => `<li>${escapeHtml(feature)}</li>`)
    .join('');
  const productLink = safeUrl(product.url);
  const images = [...new Set([product.image, ...(product.images || [])])]
    .map(safeUrl)
    .filter((url) => url !== '#');
  const image = images[0] || fallbackProduct.image;
  const imageFallbackScript = JSON.stringify(images.length ? images : fallbackProduct.images).replaceAll('</', '<\\/');
  const layoutClass = `layout-${template.layout}`;

  return `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(seoTitle)}</title>
  <meta name="description" content="${escapeHtml(seoDescription)}" />
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ${template.font};
      background: ${template.background};
      color: ${template.ink};
    }
    a { color: inherit; }
    .page {
      min-height: 100vh;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.04fr) minmax(280px, 0.96fr);
      gap: 48px;
      align-items: center;
      width: min(1160px, calc(100% - 32px));
      margin: 0 auto;
      padding: 54px 0 36px;
    }
    .layout-stack .hero { grid-template-columns: 1fr; text-align: center; }
    .layout-editorial .hero { grid-template-columns: 0.86fr 1.14fr; }
    .eyebrow {
      display: inline-flex;
      width: fit-content;
      padding: 8px 12px;
      border: 1px solid color-mix(in srgb, ${template.accent} 35%, white);
      border-radius: 999px;
      color: ${template.accent};
      font: 700 13px/1 Arial, sans-serif;
      background: color-mix(in srgb, ${template.accent} 10%, white);
    }
    h1 {
      margin: 18px 0 16px;
      max-width: 760px;
      font-size: clamp(38px, 7vw, 78px);
      line-height: 0.96;
      letter-spacing: 0;
    }
    .layout-stack h1 { margin-left: auto; margin-right: auto; }
    .tagline {
      max-width: 680px;
      margin: 0 0 16px;
      font-size: clamp(18px, 2.2vw, 25px);
      line-height: 1.45;
      color: color-mix(in srgb, ${template.ink} 72%, white);
    }
    .layout-stack .tagline, .layout-stack .description { margin-left: auto; margin-right: auto; }
    .description {
      max-width: 690px;
      font-size: 16px;
      line-height: 1.8;
      color: color-mix(in srgb, ${template.ink} 64%, white);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
      margin-top: 28px;
    }
    .layout-stack .actions { justify-content: center; }
    .button {
      display: inline-flex;
      min-height: 48px;
      align-items: center;
      justify-content: center;
      padding: 14px 20px;
      border-radius: 8px;
      background: ${template.accent};
      color: white;
      text-decoration: none;
      font: 800 15px/1 Arial, sans-serif;
    }
    .price {
      font: 800 18px/1 Arial, sans-serif;
      color: ${template.ink};
    }
    .media {
      position: relative;
      min-height: 390px;
      border-radius: 8px;
      overflow: hidden;
      background: ${template.card};
      box-shadow: 0 24px 80px rgba(15, 23, 42, 0.13);
    }
    .media img {
      width: 100%;
      height: 100%;
      min-height: 390px;
      display: block;
      object-fit: contain;
      object-position: center;
    }
    .content {
      width: min(1160px, calc(100% - 32px));
      margin: 0 auto;
      padding: 20px 0 56px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }
    section {
      background: ${template.card};
      border: 1px solid rgba(15, 23, 42, 0.09);
      border-radius: 8px;
      padding: 28px;
    }
    h2 {
      margin: 0 0 18px;
      font-size: clamp(24px, 3vw, 38px);
      letter-spacing: 0;
    }
    ul {
      margin: 0;
      padding: 0;
      display: grid;
      gap: 12px;
      list-style: none;
    }
    li {
      padding-left: 22px;
      position: relative;
      line-height: 1.55;
    }
    li::before {
      content: "";
      position: absolute;
      left: 0;
      top: 9px;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: ${template.accent};
    }
    .facts {
      display: grid;
      gap: 14px;
    }
    .fact {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(15, 23, 42, 0.08);
      font: 600 15px/1.4 Arial, sans-serif;
    }
    .fact span { color: color-mix(in srgb, ${template.ink} 58%, white); }
    .closing {
      grid-column: 1 / -1;
      display: flex;
      justify-content: space-between;
      gap: 22px;
      align-items: center;
      background: ${template.ink};
      color: white;
    }
    .closing p { margin: 0; max-width: 620px; line-height: 1.7; color: rgba(255,255,255,0.78); }
    .closing .button { background: white; color: ${template.ink}; }
    @media (max-width: 820px) {
      .hero, .content, .layout-editorial .hero {
        grid-template-columns: 1fr;
      }
      .hero { padding-top: 32px; gap: 30px; }
      .media, .media img { min-height: 290px; }
      .closing { display: grid; }
    }
  </style>
</head>
<body>
  <main class="page ${layoutClass}">
    <div class="hero">
      <div>
        <span class="eyebrow">${escapeHtml(product.availability)}</span>
        <h1>${escapeHtml(product.name)}</h1>
        <p class="tagline">${escapeHtml(product.tagline)}</p>
        <p class="description">${escapeHtml(product.description)}</p>
        <div class="actions">
          <a class="button" href="${escapeHtml(productLink)}" target="_blank" rel="noreferrer">${escapeHtml(product.cta)}</a>
          <strong class="price">${escapeHtml(product.price)}</strong>
        </div>
      </div>
      <div class="media">
        <img id="productImage" src="${escapeHtml(image)}" alt="${escapeHtml(product.name)}" />
      </div>
    </div>

    <div class="content">
      <section>
        <h2>${escapeHtml(product.sections.benefitTitle)}</h2>
        <ul>${features}</ul>
      </section>

      <section>
        <h2>${escapeHtml(product.sections.proofTitle)}</h2>
        <div class="facts">
          <div class="fact"><span>Prix</span><strong>${escapeHtml(product.price)}</strong></div>
          <div class="fact"><span>Disponibilité</span><strong>${escapeHtml(product.availability)}</strong></div>
          <div class="fact"><span>Note</span><strong>${escapeHtml(product.rating || 'Non trouvée')}</strong></div>
        </div>
      </section>

      <section class="closing">
        <div>
          <h2>${escapeHtml(product.sections.closingTitle)}</h2>
          <p>${escapeHtml(product.tagline)}</p>
        </div>
        <a class="button" href="${escapeHtml(productLink)}" target="_blank" rel="noreferrer">${escapeHtml(product.cta)}</a>
      </section>
    </div>
  </main>
  <script>
    const productImages = ${imageFallbackScript};
    const productImage = document.getElementById('productImage');
    let imageIndex = 0;
    productImage.addEventListener('error', () => {
      imageIndex += 1;
      if (imageIndex < productImages.length) {
        productImage.src = productImages[imageIndex];
      }
    });
  <\/script>
</body>
</html>`;
}

function buildMollaLandingHtml(product) {
  const productLink = safeUrl(product.url);
  const seoTitle = product.seo?.title || product.name;
  const seoDescription = product.seo?.description || product.tagline || product.description;
  const images = [...new Set([product.image, ...(product.images || [])])]
    .map(safeUrl)
    .filter((url) => url !== '#');
  const image = images[0] || fallbackProduct.image;
  const galleryImages = images.slice(0, 5);
  const imageFallbackScript = JSON.stringify(galleryImages.length ? galleryImages : [fallbackProduct.image]).replaceAll('</', '<\\/');
  const features = product.features
    .slice(0, 6)
    .map((feature) => `<div class="col-sm-6 col-lg-4"><div class="feature-box"><span></span><h3>${escapeHtml(feature)}</h3><p>${escapeHtml(product.name)} est présenté avec des informations récupérées automatiquement depuis la page produit.</p></div></div>`)
    .join('');
  const thumbs = galleryImages
    .map((src, index) => `<button class="thumb${index === 0 ? ' active' : ''}" type="button" data-src="${escapeHtml(src)}"><img src="${escapeHtml(src)}" alt="${escapeHtml(product.name)} ${index + 1}"></button>`)
    .join('');

  return `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0" />
  <title>${escapeHtml(seoTitle)}</title>
  <meta name="description" content="${escapeHtml(seoDescription)}" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css?family=Poppins:300,400,500,600,700,800&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root { --accent: #c96; --ink: #222; --muted: #777; --soft: #f7f7f7; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Poppins, Arial, sans-serif; color: var(--ink); background: #fff; }
    a { color: inherit; text-decoration: none; }
    .page-wrapper { overflow: hidden; }
    .topbar { border-bottom: 1px solid #eee; color: #777; font-size: 13px; }
    .topbar .container-xl { min-height: 42px; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    .header { position: sticky; top: 0; z-index: 10; background: rgba(255,255,255,.94); backdrop-filter: blur(14px); border-bottom: 1px solid #eee; }
    .header-inner { min-height: 76px; display: flex; align-items: center; justify-content: space-between; gap: 22px; }
    .logo { font-size: 28px; font-weight: 800; letter-spacing: 0; }
    .logo span { color: var(--accent); }
    .menu { display: flex; gap: 28px; margin: 0; padding: 0; list-style: none; font-weight: 500; color: #333; }
    .hero { background: linear-gradient(120deg, #f8f6f2 0%, #fff 58%, #f4eee7 100%); }
    .hero-grid { min-height: 680px; display: grid; grid-template-columns: minmax(0, .92fr) minmax(320px, 1.08fr); gap: 54px; align-items: center; padding: 54px 0; }
    .eyebrow { display: inline-flex; margin-bottom: 18px; color: var(--accent); font-weight: 700; text-transform: uppercase; font-size: 13px; letter-spacing: 0; }
    h1 { margin: 0 0 18px; font-size: clamp(42px, 6vw, 82px); line-height: .98; font-weight: 800; letter-spacing: 0; }
    .lead { max-width: 620px; color: #555; font-size: clamp(18px, 2vw, 24px); line-height: 1.55; }
    .description { max-width: 680px; color: var(--muted); line-height: 1.85; }
    .hero-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; margin-top: 30px; }
    .btn-molla { min-height: 48px; display: inline-flex; align-items: center; justify-content: center; padding: 13px 24px; border: 2px solid var(--accent); background: var(--accent); color: #fff; font-weight: 700; text-transform: uppercase; font-size: 13px; letter-spacing: 0; }
    .btn-molla:hover { background: #b48455; border-color: #b48455; color: #fff; }
    .price { font-size: 24px; font-weight: 800; color: #111; }
    .product-media { background: #fff; padding: 18px; box-shadow: 0 28px 90px rgba(0,0,0,.12); }
    .product-media img.main-image { width: 100%; aspect-ratio: 1 / .78; object-fit: contain; object-position: center; display: block; background: #f3f3f3; }
    .thumbs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
    .thumb { border: 2px solid transparent; padding: 0; background: #f6f6f6; cursor: pointer; aspect-ratio: 1.2 / 1; overflow: hidden; }
    .thumb.active { border-color: var(--accent); }
    .thumb img { width: 100%; height: 100%; object-fit: contain; object-position: center; display: block; }
    .thumb:has(img.is-broken) { display: none; }
    .section { padding: 76px 0; }
    .section-title { text-align: center; margin-bottom: 42px; }
    .section-title h2 { font-size: clamp(30px, 4vw, 48px); font-weight: 800; margin-bottom: 10px; letter-spacing: 0; }
    .section-title p { color: var(--muted); margin: 0 auto; max-width: 680px; line-height: 1.7; }
    .feature-box { height: 100%; padding: 28px; border: 1px solid #eee; background: #fff; transition: transform .2s ease, box-shadow .2s ease; }
    .feature-box:hover { transform: translateY(-4px); box-shadow: 0 18px 45px rgba(0,0,0,.08); }
    .feature-box span { width: 42px; height: 4px; display: block; margin-bottom: 18px; background: var(--accent); }
    .feature-box h3 { font-size: 18px; line-height: 1.45; margin: 0 0 10px; font-weight: 700; }
    .feature-box p { margin: 0; color: var(--muted); line-height: 1.7; font-size: 14px; }
    .facts { background: var(--soft); }
    .fact-card { height: 100%; padding: 28px; background: #fff; border: 1px solid #eee; text-align: center; }
    .fact-card strong { display: block; margin-top: 10px; font-size: 20px; color: #111; }
    .cta { background: #222; color: #fff; text-align: center; }
    .cta p { color: rgba(255,255,255,.72); max-width: 680px; margin: 0 auto 26px; line-height: 1.75; }
    .footer { padding: 26px 0; border-top: 1px solid #eee; color: #777; font-size: 14px; }
    @media (max-width: 900px) {
      .menu { display: none; }
      .hero-grid { min-height: auto; grid-template-columns: 1fr; gap: 32px; }
      .product-media img.main-image { aspect-ratio: 1 / .86; }
    }
  </style>
</head>
<body>
  <div class="page-wrapper">
    <div class="topbar">
      <div class="container-xl">
        <span>${escapeHtml(product.availability)}</span>
        <span>${escapeHtml(product.rating || 'Produit sélectionné')}</span>
      </div>
    </div>
    <header class="header">
      <div class="container-xl header-inner">
        <a class="logo" href="#">Mono<span>Product</span></a>
        <ul class="menu">
          <li><a href="#details" data-scroll-target="details">Détails</a></li>
          <li><a href="#features" data-scroll-target="features">Avantages</a></li>
          <li><a href="#buy" data-scroll-target="buy">Acheter</a></li>
        </ul>
        <a class="btn-molla" href="${escapeHtml(productLink)}" target="_blank" rel="noreferrer">${escapeHtml(product.cta)}</a>
      </div>
    </header>

    <main>
      <section class="hero">
        <div class="container-xl hero-grid">
          <div>
            <span class="eyebrow">${escapeHtml(product.availability)}</span>
            <h1>${escapeHtml(product.name)}</h1>
            <p class="lead">${escapeHtml(product.tagline)}</p>
            <p class="description">${escapeHtml(product.description)}</p>
            <div class="hero-actions">
              <a class="btn-molla" href="${escapeHtml(productLink)}" target="_blank" rel="noreferrer">${escapeHtml(product.cta)}</a>
              <strong class="price">${escapeHtml(product.price)}</strong>
            </div>
          </div>
          <div class="product-media">
            <img id="productImage" class="main-image" src="${escapeHtml(image)}" alt="${escapeHtml(product.name)}">
            <div class="thumbs">${thumbs}</div>
          </div>
        </div>
      </section>

      <section id="features" class="section">
        <div class="container-xl">
          <div class="section-title">
            <h2>${escapeHtml(product.sections.benefitTitle)}</h2>
            <p>${escapeHtml(product.description)}</p>
          </div>
          <div class="row g-4">${features}</div>
        </div>
      </section>

      <section id="details" class="section facts">
        <div class="container-xl">
          <div class="section-title">
            <h2>${escapeHtml(product.sections.proofTitle)}</h2>
            <p>Les informations ci-dessous sont alimentées par le scraping de l'URL fournie.</p>
          </div>
          <div class="row g-4">
            <div class="col-md-4"><div class="fact-card">Prix<strong>${escapeHtml(product.price)}</strong></div></div>
            <div class="col-md-4"><div class="fact-card">Disponibilité<strong>${escapeHtml(product.availability)}</strong></div></div>
            <div class="col-md-4"><div class="fact-card">Note<strong>${escapeHtml(product.rating || 'Non trouvée')}</strong></div></div>
          </div>
        </div>
      </section>

      <section id="buy" class="section cta">
        <div class="container-xl">
          <h2>${escapeHtml(product.sections.closingTitle)}</h2>
          <p>${escapeHtml(product.tagline)}</p>
          <a class="btn-molla" href="${escapeHtml(productLink)}" target="_blank" rel="noreferrer">${escapeHtml(product.cta)}</a>
        </div>
      </section>
    </main>

    <footer class="footer">
      <div class="container-xl d-flex flex-wrap justify-content-between gap-3">
        <span>Site mono-produit généré automatiquement</span>
        <span>${escapeHtml(product.name)}</span>
      </div>
    </footer>
  </div>
  <script>
    const productImages = ${imageFallbackScript};
    const productImage = document.getElementById('productImage');
    let imageIndex = 0;
    productImage.addEventListener('error', () => {
      imageIndex += 1;
      if (imageIndex < productImages.length) productImage.src = productImages[imageIndex];
    });
    document.querySelectorAll('.thumb').forEach((button) => {
      const thumbImage = button.querySelector('img');
      thumbImage?.addEventListener('error', () => {
        thumbImage.classList.add('is-broken');
        button.disabled = true;
        button.hidden = true;
        if (button.classList.contains('active')) {
          button.classList.remove('active');
          const nextThumb = document.querySelector('.thumb:not(:disabled)');
          nextThumb?.classList.add('active');
          if (nextThumb?.dataset.src) productImage.src = nextThumb.dataset.src;
        }
      });
      button.addEventListener('click', () => {
        if (button.disabled) return;
        document.querySelectorAll('.thumb').forEach((thumb) => thumb.classList.remove('active'));
        button.classList.add('active');
        productImage.src = button.dataset.src;
      });
    });
    document.querySelectorAll('[data-scroll-target]').forEach((link) => {
      link.addEventListener('click', (event) => {
        event.preventDefault();
        document.getElementById(link.dataset.scrollTarget)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  <\/script>
</body>
</html>`;
}

function refreshSite() {
  if (!sitePreview || !templateSelect) return;
  const templateKey = templateSelect.value;
  const product = normalizeProduct(generatedProduct);
  generatedProduct = product;
  generatedHtml = buildLandingHtml(product, templateKey);
  sitePreview.removeAttribute('src');
  sitePreview.srcdoc = '';
  sitePreview.srcdoc = generatedHtml;
  if (currentTemplate) currentTemplate.textContent = templates[templateKey].label;
  if (openProductLink) openProductLink.href = product.url;
  renderDetails(product);
  saveGeneratedSite();
}

function saveGeneratedSite() {
  if (!templateSelect) return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    product: generatedProduct,
    template: templateSelect.value,
  }));
}

function loadGeneratedSite() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (!saved) return false;

  try {
    const data = JSON.parse(saved);
    generatedProduct = normalizeProduct(data.product || fallbackProduct);
    if (!isGeneratedProductValid(generatedProduct)) {
      localStorage.removeItem(STORAGE_KEY);
      generatedProduct = { ...fallbackProduct };
      return false;
    }
    if (templateSelect && data.template && templates[data.template]) {
      templateSelect.value = data.template;
    }
    return true;
  } catch (error) {
    localStorage.removeItem(STORAGE_KEY);
    return false;
  }
}

async function scrapeProduct() {
  const user = requireAuthenticatedUser();
  if (!user) return;

  const name = productName.value.trim();
  const url = productUrl.value.trim();

  if (!name) {
    setStatus('Veuillez renseigner le nom du produit.', 'error');
    return;
  }

  if (!url) {
    setStatus('Veuillez renseigner l\'URL exacte du produit.', 'error');
    return;
  }

  if (!isValidProductUrl(url)) {
    setStatus('URL invalide. Elle doit commencer par http:// ou https://.', 'error');
    return;
  }

  setStatus('Recherche du produit, extraction des informations et génération du site...');
  scrapeButton.disabled = true;

  try {
    const response = await fetch(`${API_BASE_URL}/api/scrape`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ name, url, utilisateurId: user.id }),
    });
    const responseText = await response.text();
    let body = null;

    try {
      body = responseText ? JSON.parse(responseText) : null;
    } catch (parseError) {
      throw new Error('Réponse serveur invalide. Vérifiez que FastAPI est lancé sur http://127.0.0.1:8000.');
    }

    if (!response.ok) {
      throw new Error(formatApiDetail(body?.detail) || responseText || 'Impossible de récupérer le produit.');
    }

    generatedProduct = normalizeProduct(body.product);
    saveGeneratedSite();
    const databaseMessage = body.database?.saved ? ` Stocke dans MySQL avec l'ID ${body.database.productId}.` : '';
    setStatus(`Produit trouvé avec ${generatedProduct.images.length} image(s).${databaseMessage} Ouverture de la page des résultats...`, 'success');
    window.location.href = 'resultats.html';
  } catch (error) {
    setStatus(getReadableFetchError(error), 'error');
  } finally {
    scrapeButton.disabled = false;
  }
}

function downloadSite() {
  refreshSite();
  if (!generatedHtml) return;
  const blob = new Blob([generatedHtml], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const filename = generatedProduct.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'site-produit';
  link.href = url;
  link.download = `${filename}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

if (form) {
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    scrapeProduct();
  });
}

if (scrapeButton) scrapeButton.addEventListener('click', scrapeProduct);
if (downloadButton) downloadButton.addEventListener('click', downloadSite);
if (templateSelect) {
  templateSelect.addEventListener('change', () => {
    refreshSite();
  });
}
if (productName) {
  productName.addEventListener('input', () => {
    generatedProduct.name = productName.value.trim();
  });
}

authTabs.forEach((tab) => {
  tab.addEventListener('click', () => switchAuthTab(tab.dataset.authTab));
});

if (loginForm) {
  loginForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(loginForm);
    setAuthStatus('Connexion en cours...', 'info');
    try {
      const user = await submitAuth('login', {
        email: formData.get('email'),
        motDePasse: formData.get('motDePasse'),
      });
      setCurrentUser(user);
      setAuthStatus('Connexion reussie. Vous pouvez generer un produit.', 'success');
    } catch (error) {
      setAuthStatus(getReadableFetchError(error), 'error');
    }
  });
}

if (registerForm) {
  registerForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(registerForm);
    setAuthStatus('Creation du compte...', 'info');
    try {
      const user = await submitAuth('register', {
        nom: formData.get('nom'),
        email: formData.get('email'),
        motDePasse: formData.get('motDePasse'),
      });
      setCurrentUser(user);
      setAuthStatus('Compte cree. Vous pouvez generer un produit.', 'success');
    } catch (error) {
      setAuthStatus(getReadableFetchError(error), 'error');
    }
  });
}

if (logoutButton) {
  logoutButton.addEventListener('click', () => {
    setCurrentUser(null);
    setAuthStatus('Vous etes deconnecte. Connectez-vous pour utiliser le generateur.', 'info');
  });
}

if (startButton) {
  startButton.addEventListener('click', () => {
    const target = getCurrentUser() ? generatorPanel : authPanel;
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

window.addEventListener('DOMContentLoaded', () => {
  updateAuthView();
  const hasPreview = Boolean(sitePreview);
  if (!hasPreview) {
    if (authPanel) switchAuthTab('login');
    return;
  }

  if (!requireAuthenticatedUser()) return;

  const loaded = loadGeneratedSite();
  if (!loaded) {
    setStatus('Aucun produit généré. Retournez au formulaire pour créer un site.', 'error');
    return;
  }
  refreshSite();
});
