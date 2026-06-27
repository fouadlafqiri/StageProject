import { Component, OnInit } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';

import { Product, ProductService } from '../../../product.service';

@Component({
  selector: 'app-product-preview-page',
  templateUrl: './product-preview.page.html',
  styleUrls: ['./product-preview.page.css'],
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
})
export class ProductPreviewPageComponent implements OnInit {
  product: Product | null = null;
  selectedTemplate = 'minimal';
  safeSrcDoc: SafeHtml | null = null;
  fetchingMore = false;
  totalImages = 0;
  fetchStatus = '';
  templates = [
    { key: 'minimal', label: 'Minimaliste', description: 'Sobre et direct' },
    { key: 'tech', label: 'High-Tech', description: 'Net et dynamique' },
    { key: 'boutique', label: 'Boutique Premium', description: 'Elegant et editorial' },
    { key: 'molla', label: 'E-commerce Moderne', description: 'Retail complet' },
  ];

  constructor(private productService: ProductService, private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    this.productService.generatedProduct$.subscribe((p: Product | null) => {
      this.product = p;
      this.totalImages = (p?.images || []).length;
      this.refreshPreview();
    });
  }

  searchByImageUrl(imageUrl: string) {
    if (!this.product || !this.product.name || !imageUrl) return;

    const user = (this.productService as any).userSubject?.value;
    if (!user?.id) {
      this.fetchStatus = '❌ Veuillez vous connecter pour utiliser cette fonctionnalité.';
      return;
    }

    this.fetchingMore = true;
    this.fetchStatus = '🔍 Recherche du produit à partir de cette image...';

    this.productService.searchProductByImageUrl(imageUrl, this.product.name, user.id).subscribe({
      next: (res: any) => {
        this.fetchingMore = false;
        if (res.status === 'ok' && res.product) {
          const updatedProduct = res.product;
          this.product = updatedProduct;
          this.totalImages = (updatedProduct.images || []).length;
          this.fetchStatus = res.found_url
            ? `✅ Produit trouvé ! URL: ${res.found_url}`
            : '✅ Produit mis à jour avec les informations trouvées.';
          this.refreshPreview();
        } else {
          this.fetchStatus = '⚠️ Aucune information supplémentaire trouvée pour cette image.';
        }
      },
      error: (err: any) => {
        this.fetchingMore = false;
        this.fetchStatus = '❌ Erreur: ' + (err.error?.detail || err.message || 'Recherche par image échouée.');
      },
    });
  }

  fetchMoreImages() {
    if (!this.product || !this.product.name || this.fetchingMore) return;

    this.fetchingMore = true;
    this.fetchStatus = 'Recherche d\'images supplémentaires...';

    const productId = (this.product as any).id || 0;
    const existingImages = this.product.images || [];

    this.productService.fetchMoreImages(productId, this.product.name, existingImages).subscribe({
      next: (res: any) => {
        this.fetchingMore = false;
        if (res.status === 'ok' && res.images) {
          // Merge new images into current product
          const updatedProduct = { ...this.product! };
          updatedProduct.images = res.images;
          if (updatedProduct.images.length > 0 && (!updatedProduct.image || updatedProduct.image === '')) {
            updatedProduct.image = updatedProduct.images[0];
          }
          this.product = updatedProduct;
          this.totalImages = res.images.length;
          this.fetchStatus = `✅ ${res.new_images || 0} nouvelle(s) image(s) ajoutée(s) (total: ${this.totalImages})`;
          // Refresh the preview with new images
          this.refreshPreview();
          // Save back to service
          (this.productService as any).saveGeneratedProduct(updatedProduct);
        } else {
          this.fetchStatus = 'Aucune image supplémentaire trouvée.';
        }
      },
      error: (err: any) => {
        this.fetchingMore = false;
        this.fetchStatus = '❌ Erreur: ' + (err.error?.detail || err.message || 'Impossible de récupérer plus d\'images.');
      },
    });
  }

  onImageError(event: Event) {
    const img = event.target as HTMLImageElement;
    if (img) {
      img.style.display = 'none';
    }
  }

  templateLabel(templateKey: string) {
    const map: Record<string, string> = {
      minimal: 'Minimaliste',
      tech: 'High-Tech',
      boutique: 'Boutique Premium',
      molla: 'E-commerce Moderne',
    };
    return map[templateKey] || templateKey;
  }

  detailRows() {
    if (!this.product) return [];
    const p = this.product;

    return [
      { label: 'Nom', value: p.name },
      { label: 'Marque', value: p.brand || 'Non trouvée' },
      { label: 'Catégorie', value: p.category || 'Non trouvée' },
      { label: 'Prix', value: p.price },
      { label: 'Disponibilité', value: p.availability },
      { label: 'Note', value: p.rating || 'Non trouvée' },
      {
        label: 'Similarité',
        value: typeof p.similarityScore === 'number' ? `${Math.round(p.similarityScore)}%` : 'Non calculée',
      },
      {
        label: 'Contenu',
        value: p.contentSource === 'claude' ? 'Claude API' : 'Génération locale',
      },
      { label: 'Template', value: this.templateLabel(this.selectedTemplate) },
    ];
  }

  technicalSpecs() {
    return (this.product?.technicalSpecs || []).filter((spec) => spec?.label && spec?.value).slice(0, 8);
  }

  reviews() {
    return (this.product?.reviews || []).filter((review) => review?.rating || review?.text).slice(0, 4);
  }

  refreshPreview() {
    if (!this.product) return;
    const html = this.productService.buildLandingHtml(this.product, this.selectedTemplate);
    this.safeSrcDoc = this.sanitizer.bypassSecurityTrustHtml(html);
  }

  download() {
    if (!this.product || typeof window === 'undefined') return;

    const html = this.productService.buildLandingHtml(this.product, this.selectedTemplate);
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${this.product.name.replace(/\s+/g, '-').replace(/(^-|-$)/g, '')}.html`;
    a.click();
    window.URL.revokeObjectURL(url);
  }
}
