import { Component, OnInit } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';

import { Product, ProductService } from '../product.service';

@Component({
  selector: 'app-results',
  templateUrl: './results.component.html',
  styleUrls: ['./results.component.css'],
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
})
export class ResultsComponent implements OnInit {
  product: Product | null = null;
  selectedTemplate = 'minimal';
  safeSrcDoc: SafeHtml | null = null;

  constructor(private productService: ProductService, private sanitizer: DomSanitizer) {}

  ngOnInit(): void {
    this.productService.generatedProduct$.subscribe((p) => {
      this.product = p;
      this.refreshPreview();
    });
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
      { label: 'Prix', value: p.price },
      { label: 'Disponibilité', value: p.availability },
      { label: 'Note', value: p.rating || 'Non trouvée' },
      {
        label: 'Contenu',
        value: p.contentSource === 'claude' ? 'Claude API' : 'Génération locale',
      },
      { label: 'Template', value: this.templateLabel(this.selectedTemplate) },
    ];
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
