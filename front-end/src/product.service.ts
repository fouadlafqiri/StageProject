import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, throwError, timeout } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

export interface Product {
  name: string;
  tagline: string;
  description: string;
  price: string;
  image: string;
  images: string[];
  features: string[];
  benefits?: string[];
  faq?: Array<{ question: string; answer: string }>;
  ctaTexts?: string[];
  cta: string;
  url: string;
  availability: string;
  rating: string;
  brand?: string;
  category?: string;
  technicalSpecs?: Array<{ label: string; value: string }>;
  reviews?: Array<{ author?: string; rating?: string; text?: string }>;
  similarityScore?: number;
  uploadedReferenceImage?: string;
  sections: any;
  contentSource?: string;
  // backend may optionally return this
  seo?: { title?: string; description?: string };
}

type TemplateKey = 'minimal' | 'tech' | 'boutique' | 'molla';

@Injectable({
  providedIn: 'root',
})
export class ProductService {
  private readonly STORAGE_KEY = 'monoProductGeneratedSite';
  private readonly AUTH_KEY = 'monoProductUser';
  private static readonly DEFAULT_API_URL = 'http://127.0.0.1:8000/api';

  public userSubject = new BehaviorSubject<any>(this.getStoredUser());
  user$ = this.userSubject.asObservable();

  private get apiUrl(): string {
    if (typeof window === 'undefined') {
      return '/api';
    }

    const host = window.location.hostname;
    const port = window.location.port;

    if (host === 'localhost' || host === '127.0.0.1') {
      return port === '8000' ? '/api' : ProductService.DEFAULT_API_URL;
    }

    return '/api';
  }

  private generatedProductSubject = new BehaviorSubject<Product | null>(this.getStoredProduct());
  generatedProduct$ = this.generatedProductSubject.asObservable();

  constructor(private http: HttpClient) {}

  private isBrowser(): boolean {
    return typeof window !== 'undefined' && typeof localStorage !== 'undefined';
  }


  login(credentials: { email: string; motDePasse: string }): Observable<any> {
    return this.http.post(`${this.apiUrl}/auth/login`, credentials).pipe(
      this.withApiTimeout(),
      tap((res: any) => this.setCurrentUser(res.user)),
    );
  }

  register(payload: any): Observable<any> {
    return this.http.post(`${this.apiUrl}/auth/register`, payload).pipe(
      this.withApiTimeout(),
      tap((res: any) => this.setCurrentUser(res.user))
    );
  }

  logout() {
    if (!this.isBrowser()) {
      this.userSubject.next(null);
      return;
    }

    localStorage.removeItem(this.AUTH_KEY);
    this.userSubject.next(null);
  }

  private setCurrentUser(user: any) {
    if (this.isBrowser()) {
      localStorage.setItem(this.AUTH_KEY, JSON.stringify(user));
    }
    this.userSubject.next(user);
  }

  private getStoredUser() {
    if (!this.isBrowser()) return null;

    const user = localStorage.getItem(this.AUTH_KEY);
    return user ? JSON.parse(user) : null;
  }


  scrapeProduct(name: string, url: string, userId: number): Observable<any> {
    return this.http
      .post(`${this.apiUrl}/scrape`, { name, url, utilisateurId: userId })
      .pipe(
        this.withApiTimeout(60000),
        tap((res: any) => this.saveGeneratedProduct(res.product))
      );
  }

  validateProductImage(file: File, name: string = ''): Observable<{ isValid: boolean; message: string }> {
    const formData = new FormData();
    formData.append('file', file);
    if (name) {
      formData.append('name', name);
    }

    return this.http
      .post<{ isValid: boolean; message: string }>(`${this.apiUrl}/validate-image`, formData)
      .pipe(
        this.withApiTimeout(20000),
        catchError((err) => {
          if (err.status === 0 || err.name === 'TimeoutError') {
            return throwError(() => ({
              isValid: false,
              message: 'Impossible de contacter le serveur pour valider l\'image.',
            }));
          }
          return throwError(() => ({
            isValid: false,
            message: 'Erreur lors de la validation de l\'image.',
          }));
        })
      );
  }

  fetchMoreImages(productId: number, name: string, existingImages: string[] = []): Observable<any> {
    return this.http
      .post(`${this.apiUrl}/product/${productId}/images/more`, { name, existing_images: existingImages })
      .pipe(this.withApiTimeout(30000));
  }

  searchProductByImageUrl(imageUrl: string, name: string, userId: number): Observable<any> {
    return this.http
      .post(`${this.apiUrl}/product/search-by-image-url`, {
        imageUrl,
        name,
        utilisateurId: userId,
      })
      .pipe(
        this.withApiTimeout(60000),
        tap((res: any) => {
          const product = res.product;
          if (product && imageUrl) {
            product.uploadedReferenceImage = imageUrl;
          }
          this.saveGeneratedProduct(product);
        })
      );
  }

  searchProductByImage(file: File, name: string, userId: number, previewImage = ''): Observable<any> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name || '');
    formData.append('utilisateurId', userId.toString());

    return this.http
      .post(`${this.apiUrl}/search-by-image`, formData)
      .pipe(
        this.withApiTimeout(60000),
        tap((res: any) => {
          const product = res.product;
          if (product && previewImage) {
            product.uploadedReferenceImage = previewImage;
          }
          this.saveGeneratedProduct(product);
        })
      );
  }

  validateProductNameUrl(name: string, url: string): { isValid: boolean; message: string } {
    if (!name.trim()) {
      return { isValid: false, message: 'Veuillez entrer le nom du produit.' };
    }
    if (!url.trim()) {
      return { isValid: false, message: 'Veuillez entrer l\'URL du produit.' };
    }

    const nameTokens = this.extractProductTokens(name);
    if (nameTokens.length === 0) {
      return { isValid: false, message: 'Le nom du produit est trop court ou invalide.' };
    }

    const normalizedUrl = url.toLowerCase().replace(/[^a-z0-9]/g, '');
    const matchedTokens = nameTokens.filter((token) => normalizedUrl.includes(token));

    if (matchedTokens.length === 0) {
      const tokenList = nameTokens.join(', ');
      return {
        isValid: false,
        message: `ERREUR: Le nom du produit "${name}" ne correspond PAS à l'URL. Les termes "${tokenList}" ne se trouvent pas dans l'URL. Vérifiez que vous avez copié la BONNE URL du produit.`,
      };
    }

    const urlDomain = url.split('/')[2] || '';
    const isAppleProduct = nameTokens.some((t) => ['iphone', 'ipad', 'mac', 'macbook', 'airpods', 'apple'].includes(t));
    const isSamsungProduct = nameTokens.some((t) => ['samsung', 'galaxy', 'note'].includes(t));

    if (isAppleProduct && !urlDomain.includes('apple')) {
      return {
        isValid: false,
        message: `ERREUR: "${name}" est un produit Apple, mais l'URL n'est pas apple.com. Vérifiez l'URL.`,
      };
    }

    if (isSamsungProduct && !urlDomain.includes('samsung')) {
      return {
        isValid: false,
        message: `ERREUR: "${name}" est un produit Samsung, mais l'URL n'est pas samsung.com. Vérifiez l'URL.`,
      };
    }

    return { isValid: true, message: '' };
  }

  private extractProductTokens(name: string): string[] {
    const stopWords = new Set(['le', 'la', 'les', 'de', 'du', 'et', 'ou', 'par', 'pour', 'avec', 'sur', 'en', 'the', 'a', 'an', 'or', 'and', 'by', 'with', 'new', 'plus']);

    const tokens = name
      .toLowerCase()
      .split(/[\s\-_.,()]+/)
      .filter((token) => token.length >= 2 && !stopWords.has(token))
      .map((token) => token.replace(/[^a-z0-9]/g, ''));

    return tokens
      .sort((a, b) => {
        const aHasNum = /\d/.test(a);
        const bHasNum = /\d/.test(b);
        if (aHasNum !== bHasNum) return aHasNum ? -1 : 1;
        return b.length - a.length;
      })
      .filter((t) => t.length > 0)
      .slice(0, 8);
  }

  private withApiTimeout<T>(milliseconds = 10000) {
    return (source: Observable<T>) =>
      source.pipe(
        timeout(milliseconds),
        catchError((err) => {
          if (err?.name === 'TimeoutError' || err?.status === 0) {
            return throwError(() => ({
              ...err,
              error: {
                detail: "Backend indisponible. Lancez l'API FastAPI sur http://127.0.0.1:8000 puis réessayez.",
              },
            }));
          }

          return throwError(() => err);
        })
      );
  }

  private saveGeneratedProduct(product: Product) {
    if (this.isBrowser()) {
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(product));
    }
    this.generatedProductSubject.next(product);
  }

  private getStoredProduct(): Product | null {
    if (!this.isBrowser()) return null;

    const data = localStorage.getItem(this.STORAGE_KEY);
    return data ? JSON.parse(data) : null;
  }


  buildLandingHtml(product: Product, templateKey: string): string {
    const fallbackProduct: Product = {
      name: 'Produit exemple',
      tagline: "Une page mono-produit générée automatiquement à partir d'une URL.",
      description:
        "Le générateur récupère les données disponibles sur la page produit puis construit un site complet avec image, prix, bénéfices et appel à l'action.",
      price: 'Prix à récupérer',
      image: 'https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80',
      images: ['https://images.unsplash.com/photo-1516321318423-f06f85e504b3?auto=format&fit=crop&w=1200&q=80'],
      features: [
        'Scraping des métadonnées',
        'Template personnalisable',
        'Page HTML téléchargeable',
        'Lien vers la page officielle',
      ],
      benefits: [
        'Une page claire pour présenter le produit sans distraction',
        'Des arguments d achat structurés pour rassurer le visiteur',
        'Une galerie pensée pour inspecter rapidement le produit',
      ],
      faq: [
        { question: 'Ou acheter le produit ?', answer: 'Le bouton principal renvoie vers la page produit originale.' },
        { question: 'Les informations sont-elles fiables ?', answer: 'Elles sont extraites depuis la source puis enrichies pour la présentation.' },
      ],
      ctaTexts: ['Voir le produit', 'Acheter maintenant'],
      cta: 'Voir le produit',
      url: '#',
      availability: 'Disponibilité à vérifier',
      rating: '',
      sections: {
        benefitTitle: 'Pourquoi choisir ce produit ?',
        proofTitle: 'Informations produit',
        closingTitle: "Prêt à passer à l'action ?",
      },
      seo: {
        title: 'Produit exemple',
        description: 'Page mono-produit',
      },
      contentSource: 'fallback',
    };

    const normalized = this.normalizeProduct(product, fallbackProduct);
    const key = (templateKey as TemplateKey) || 'minimal';

    if (key === 'molla') {
      return this.buildMollaLandingHtml(normalized);
    }

    return this.buildSharedLandingHtml(normalized, key);
  }

  // ---------- helpers (HTML generation) ----------

  private templates: Record<
    TemplateKey,
    {
      label: string;
      accent: string;
      background: string;
      ink: string;
      card: string;
      font: string;
      layout: 'split' | 'stack' | 'editorial';
    }
  > = {
    minimal: {
      label: 'Minimaliste',
      accent: '#0f766e',
      background: '#f7faf9',
      ink: '#10201d',
      card: '#ffffff',
      font: 'Inter, Arial, sans-serif',
      layout: 'split',
    },
    tech: {
      label: 'High-Tech',
      accent: '#2563eb',
      background: '#eef4ff',
      ink: '#111827',
      card: '#ffffff',
      font: 'Segoe UI, Arial, sans-serif',
      layout: 'stack',
    },
    boutique: {
      label: 'Boutique Premium',
      accent: '#be123c',
      background: '#fff7f8',
      ink: '#241116',
      card: '#ffffff',
      font: 'Georgia, Times New Roman, serif',
      layout: 'editorial',
    },
    molla: {
      label: 'E-commerce Moderne',
      accent: '#c96',
      background: '#ffffff',
      ink: '#222222',
      card: '#ffffff',
      font: 'Poppins, Arial, sans-serif',
      layout: 'split',
    },
  };

  private normalizeProduct(product: Product, fallback: Product): Product {
    const seo = product?.seo ?? fallback.seo;

    const images = (Array.isArray(product.images) ? product.images : [])
      .filter(Boolean)
      .map((u) => this.safeUrl(u))
      .filter((u) => u !== '#');

    const image = this.safeUrl(product?.image || '') === '#' ? fallback.image : this.safeUrl(product?.image || fallback.image);

    const imagesMerged = [...new Set([image, ...images])];
    const pickedImages = imagesMerged.length ? imagesMerged.slice(0, 5) : [fallback.image];
    const specFeatures = (product?.technicalSpecs || [])
      .filter((spec) => spec?.label && spec?.value)
      .map((spec) => `${spec.label}: ${spec.value}`);
    const productFeatures = Array.isArray(product?.features) ? product.features : [];
    const mergedFeatures = [...new Set([...productFeatures, ...specFeatures])];

    return {
      ...fallback,
      ...product,
      seo,
      url: this.safeUrl(product?.url || '') === '#' ? fallback.url : this.safeUrl(product?.url || ''),
      image: image,
      images: pickedImages,
      features: mergedFeatures.length ? mergedFeatures.slice(0, 6) : fallback.features,
      sections: { ...(fallback.sections || {}), ...(product.sections || {}) },
      rating: product.rating || fallback.rating,
      availability: product.availability || fallback.availability,
      cta: product.cta || fallback.cta,
    };
  }

  private escapeHtml(value = ''): string {
    return String(value)
      .replaceAll('&', '&')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  private safeUrl(value = '#'): string {
    const url = String(value || '#').trim();
    if (url.startsWith('//')) return `https:${url}`;
    if (url.startsWith('data:')) return url;
    if (url.startsWith('/uploads/')) return `${this.assetBaseUrl()}${url}`;
    return url.startsWith('http://') || url.startsWith('https://') ? url : '#';
  }

  private assetBaseUrl(): string {
    if (typeof window === 'undefined') {
      return '';
    }

    const { protocol, hostname, port } = window.location;
    if ((hostname === 'localhost' || hostname === '127.0.0.1') && port !== '8000') {
      return ProductService.DEFAULT_API_URL.replace(/\/api$/, '');
    }

    return `${protocol}//${window.location.host}`;
  }

  private buildSharedLandingHtml(product: Product, templateKey: Exclude<TemplateKey, 'molla'>): string {
    const template = this.templates[templateKey];
    const seoTitle = product.seo?.title || product.name;
    const seoDescription = product.seo?.description || product.tagline || product.description;

    const features = (product.features || [])
      .slice(0, 6)
      .map((f) => `<li>${this.escapeHtml(f)}</li>`)
      .join('');
    const benefits = (product.benefits || product.features || [])
      .slice(0, 5)
      .map((benefit) => `<li>${this.escapeHtml(benefit)}</li>`)
      .join('');
    const faq = (product.faq || [])
      .slice(0, 4)
      .map(
        (item) =>
          `<details><summary>${this.escapeHtml(item.question)}</summary><p>${this.escapeHtml(item.answer)}</p></details>`
      )
      .join('');
    const productLink = this.safeUrl(product.url);
    const images = [...new Set([product.image, ...(product.images || [])])]
      .map((u) => this.safeUrl(u))
      .filter((u) => u !== '#' && u);

    const image = images[0] || product.image;
    const galleryImages = images.slice(0, 5);
    const imageFallbackScript = JSON.stringify(galleryImages.length ? galleryImages : [product.image]).replaceAll('</', '<\\/');

    const layoutClass = `layout-${template.layout}`;

    // Build gallery thumbnails
    const thumbs = galleryImages
      .map((src, index) =>
        `<button class="thumb${index === 0 ? ' active' : ''}" type="button" data-src="${this.escapeHtml(src)}" ${index >= 5 ? 'style="display:none"' : ''}><img src="${this.escapeHtml(src)}" alt="${this.escapeHtml(product.name)} ${index + 1}" onerror="this.parentElement.style.display='none'"></button>`
      )
      .join('');

    const hasGallery = galleryImages.length > 1;

    return `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${this.escapeHtml(seoTitle)}</title>
  <meta name="description" content="${this.escapeHtml(seoDescription)}" />
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ${template.font};
      background: ${template.background};
      color: ${template.ink};
    }
    a { color: inherit; }
    .page { min-height: 100vh; }
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
    .thumbs {
      display: ${hasGallery ? 'grid' : 'none'};
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
      padding: 0 4px;
    }
    .thumb {
      border: 2px solid transparent;
      padding: 0;
      background: ${template.card};
      cursor: pointer;
      aspect-ratio: 1.2 / 1;
      overflow: hidden;
      border-radius: 4px;
      transition: border-color 0.2s;
    }
    .thumb.active {
      border-color: ${template.accent};
    }
    .thumb img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center;
      display: block;
    }
    .thumb[hidden] { display: none; }
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
    details {
      padding: 16px 0;
      border-bottom: 1px solid rgba(15, 23, 42, 0.08);
    }
    details:last-child { border-bottom: 0; }
    summary {
      cursor: pointer;
      font-weight: 800;
      line-height: 1.45;
    }
    details p {
      margin: 10px 0 0;
      color: color-mix(in srgb, ${template.ink} 62%, white);
      line-height: 1.7;
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
      .hero, .content, .layout-editorial .hero { grid-template-columns: 1fr; }
      .hero { padding-top: 32px; gap: 30px; }
      .media, .media img { min-height: 290px; }
      .thumbs { grid-template-columns: repeat(4, 1fr); }
      .closing { display: grid; }
    }
  </style>
</head>
<body>
  <main class="page ${layoutClass}">
    <div class="hero">
      <div>
        <span class="eyebrow">${this.escapeHtml(product.availability)}</span>
        <h1>${this.escapeHtml(product.name)}</h1>
        <p class="tagline">${this.escapeHtml(product.tagline)}</p>
        <p class="description">${this.escapeHtml(product.description)}</p>
        <div class="actions">
          <a class="button" href="${this.escapeHtml(productLink)}" target="_blank" rel="noreferrer">${this.escapeHtml(product.cta)}</a>
          <strong class="price">${this.escapeHtml(product.price)}</strong>
        </div>
      </div>
      <div class="media">
        <img id="productImage" src="${this.escapeHtml(image)}" alt="${this.escapeHtml(product.name)}" />
        <div class="thumbs">${thumbs}</div>
      </div>
    </div>

    <div class="content">
      <section>
        <h2>${this.escapeHtml(product.sections.benefitTitle)}</h2>
        <ul>${features}</ul>
      </section>

      <section>
        <h2>${this.escapeHtml(product.sections.proofTitle)}</h2>
        <div class="facts">
          <div class="fact"><span>Prix</span><strong>${this.escapeHtml(product.price)}</strong></div>
          <div class="fact"><span>Disponibilité</span><strong>${this.escapeHtml(product.availability)}</strong></div>
          <div class="fact"><span>Note</span><strong>${this.escapeHtml(product.rating || 'Non trouvée')}</strong></div>
        </div>
      </section>

      <section>
        <h2>Benefices client</h2>
        <ul>${benefits}</ul>
      </section>

      <section>
        <h2>Questions frequentes</h2>
        ${faq || `<p class="description">Consultez la page officielle pour les details de livraison, garantie et disponibilite.</p>`}
      </section>

      <section class="closing">
        <div>
          <h2>${this.escapeHtml(product.sections.closingTitle)}</h2>
          <p>${this.escapeHtml(product.tagline)}</p>
        </div>
        <a class="button" href="${this.escapeHtml(productLink)}" target="_blank" rel="noreferrer">${this.escapeHtml(product.cta)}</a>
      </section>
    </div>
  </main>
  <script>
    const productImages = ${imageFallbackScript};
    const productImage = document.getElementById('productImage');
    let imageIndex = 0;
    productImage.addEventListener('error', () => {
      imageIndex += 1;
      if (imageIndex < productImages.length) productImage.src = productImages[imageIndex];
    });
    document.querySelectorAll('.thumb').forEach((button) => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.thumb').forEach((thumb) => thumb.classList.remove('active'));
        button.classList.add('active');
        productImage.src = button.dataset.src;
      });
    });
  <\/script>
</body>
</html>`;
  }

  private buildMollaLandingHtml(product: Product): string {
    // Directly adapted from script.js (trimmed to essentials but keeps the look + script)
    const productLink = this.safeUrl(product.url);
    const seoTitle = product.seo?.title || product.name;
    const seoDescription = product.seo?.description || product.tagline || product.description;

    const images = [...new Set([product.image, ...(product.images || [])])]
      .map((u) => this.safeUrl(u))
      .filter((u) => u !== '#');

    const image = images[0] || product.image;
    const galleryImages = images.slice(0, 5);
    const imageFallbackScript = JSON.stringify(galleryImages.length ? galleryImages : [product.image]).replaceAll('</', '<\\/');

    const features = (product.features || [])
      .slice(0, 6)
      .map(
        (feature) =>
          `<div class="col-sm-6 col-lg-4"><div class="feature-box"><span></span><h3>${this.escapeHtml(
            feature
          )}</h3><p>${this.escapeHtml(product.name)} est présenté avec des informations récupérées automatiquement depuis la page produit.</p></div></div>`
      )
      .join('');

    const benefits = (product.benefits || product.features || [])
      .slice(0, 4)
      .map(
        (benefit) =>
          `<div class="col-md-6"><div class="fact-card">${this.escapeHtml(benefit)}</div></div>`
      )
      .join('');

    const faq = (product.faq || [])
      .slice(0, 4)
      .map(
        (item) =>
          `<details><summary>${this.escapeHtml(item.question)}</summary><p>${this.escapeHtml(item.answer)}</p></details>`
      )
      .join('');

    const thumbs = galleryImages
      .map(
        (src, index) =>
          `<button class="thumb${index === 0 ? ' active' : ''}" type="button" data-src="${this.escapeHtml(
            src
          )}"><img src="${this.escapeHtml(src)}" alt="${this.escapeHtml(product.name)} ${index + 1}"></button>`
      )
      .join('');

    return `<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0" />
  <title>${this.escapeHtml(seoTitle)}</title>
  <meta name="description" content="${this.escapeHtml(seoDescription)}" />
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
    details { padding: 18px 0; border-bottom: 1px solid #eee; }
    details:last-child { border-bottom: 0; }
    summary { cursor: pointer; font-weight: 700; }
    details p { margin: 10px 0 0; color: var(--muted); line-height: 1.7; }
    .cta { background: #222; color: #fff; text-align: center; }
    .cta p { color: rgba(255,255,255,.72); max-width: 680px; margin: 0 auto 26px; line-height: 1.75; }
    .footer { padding: 26px 0; border-top: 1px solid #eee; color: #777; font-size: 14px; }
  </style>
</head>
<body>
  <div class="page-wrapper">
    <div class="topbar">
      <div class="container-xl">
        <span>${this.escapeHtml(product.availability)}</span>
        <span>${this.escapeHtml(product.rating || 'Produit sélectionné')}</span>
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
        <a class="btn-molla" href="${this.escapeHtml(productLink)}" target="_blank" rel="noreferrer">${this.escapeHtml(product.cta)}</a>
      </div>
    </header>

    <main>
      <section class="hero">
        <div class="container-xl hero-grid">
          <div>
            <span class="eyebrow">${this.escapeHtml(product.availability)}</span>
            <h1>${this.escapeHtml(product.name)}</h1>
            <p class="lead">${this.escapeHtml(product.tagline)}</p>
            <p class="description">${this.escapeHtml(product.description)}</p>
            <div class="hero-actions">
              <a class="btn-molla" href="${this.escapeHtml(productLink)}" target="_blank" rel="noreferrer">${this.escapeHtml(product.cta)}</a>
              <strong class="price">${this.escapeHtml(product.price)}</strong>
            </div>
          </div>

          <div class="product-media">
            <img id="productImage" class="main-image" src="${this.escapeHtml(image)}" alt="${this.escapeHtml(product.name)}">
            <div class="thumbs">${thumbs}</div>
          </div>
        </div>
      </section>

      <section id="features" class="section">
        <div class="container-xl">
          <div class="section-title">
            <h2>${this.escapeHtml(product.sections.benefitTitle)}</h2>
            <p>${this.escapeHtml(product.description)}</p>
          </div>
          <div class="row g-4">${features}</div>
        </div>
      </section>

      <section id="details" class="section facts">
        <div class="container-xl">
          <div class="section-title">
            <h2>${this.escapeHtml(product.sections.proofTitle)}</h2>
            <p>Les informations ci-dessous sont alimentées par le scraping de l'URL fournie.</p>
          </div>
          <div class="row g-4">
            <div class="col-md-4"><div class="fact-card">Prix<strong>${this.escapeHtml(product.price)}</strong></div></div>
            <div class="col-md-4"><div class="fact-card">Disponibilité<strong>${this.escapeHtml(product.availability)}</strong></div></div>
            <div class="col-md-4"><div class="fact-card">Note<strong>${this.escapeHtml(product.rating || 'Non trouvée')}</strong></div></div>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="container-xl">
          <div class="section-title">
            <h2>Benefices client</h2>
            <p>Une presentation orientee conversion pour aider vos visiteurs a comprendre la valeur du produit.</p>
          </div>
          <div class="row g-4">${benefits}</div>
        </div>
      </section>

      <section class="section facts">
        <div class="container-xl">
          <div class="section-title">
            <h2>Questions frequentes</h2>
            <p>Les reponses essentielles avant le passage a l'action.</p>
          </div>
          ${faq || '<p>Consultez la page officielle pour les details de livraison, garantie et disponibilite.</p>'}
        </div>
      </section>

      <section id="buy" class="section cta">
        <div class="container-xl">
          <h2>${this.escapeHtml(product.sections.closingTitle)}</h2>
          <p>${this.escapeHtml(product.tagline)}</p>
          <a class="btn-molla" href="${this.escapeHtml(productLink)}" target="_blank" rel="noreferrer">${this.escapeHtml(product.cta)}</a>
        </div>
      </section>
    </main>

    <footer class="footer">
      <div class="container-xl d-flex flex-wrap justify-content-between gap-3">
        <span>Site mono-produit généré automatiquement</span>
        <span>${this.escapeHtml(product.name)}</span>
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
}
