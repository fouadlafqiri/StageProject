import { Component } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { type Observable } from 'rxjs';

import { ProductService } from '../../../product.service';

type ProductForm = { name: string; url: string; template: string };
type TemplateOption = { key: string; label: string; description: string; tone: string };

@Component({
  selector: 'app-product-generator-page',
  templateUrl: './product-generator.page.html',
  styleUrls: ['./product-generator.page.css'],
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
})
export class ProductGeneratorPageComponent {
  user$!: Observable<any>;
  productForm: ProductForm = { name: '', url: '', template: 'minimal' };
  loading = false;
  status = '';
  searchMode: 'url' | 'image' = 'url';
  selectedImage: File | null = null;
  imagePreview: string = '';
  templates: TemplateOption[] = [
    { key: 'minimal', label: 'Minimaliste', description: 'Calme, clair, ideal pour un produit premium simple.', tone: 'Sobre' },
    { key: 'tech', label: 'High-Tech', description: 'Contraste net, sections efficaces, parfait pour electronique.', tone: 'Dynamique' },
    { key: 'boutique', label: 'Boutique Premium', description: 'Presentation elegante pour mode, beaute et accessoires.', tone: 'Editorial' },
    { key: 'molla', label: 'E-commerce Moderne', description: 'Look boutique en ligne avec navigation et galerie forte.', tone: 'Retail' },
  ];

  constructor(private productService: ProductService, public router: Router) {
    this.user$ = this.productService.user$;
  }

  goToLogin() {
    this.router.navigate(['/login']);
  }

  onLogout() {
    this.productService.logout();
    this.status = 'Vous êtes déconnecté. Connectez-vous pour utiliser le générateur.';
  }

  onImageSelected(event: any) {
    const file = event.target.files?.[0];
    if (file) {
      // Validate file
      if (!file.type.startsWith('image/')) {
        this.status = 'Erreur : Veuillez sélectionner une image (JPEG, PNG, etc.)';
        return;
      }
      if (file.size > 5 * 1024 * 1024) {
        this.status = 'Erreur : Image trop grande (max 5MB)';
        return;
      }

      this.selectedImage = file;
      const reader = new FileReader();
      reader.onload = (e) => {
        this.imagePreview = (e.target?.result as string) || '';
      };
      reader.readAsDataURL(file);
    }
  }

  clearImage() {
    this.selectedImage = null;
    this.imagePreview = '';
  }

  generate() {
    const user = this.productService.userSubject.value;
    if (!user?.id) {
      this.status = 'Veuillez vous connecter pour générer un site.';
      return;
    }

    if (this.searchMode === 'image') {
      this.generateFromImage(user.id);
    } else {
      this.generateFromUrl(user.id);
    }
  }

  private generateFromUrl(userId: number) {
    const validation = this.productService.validateProductNameUrl(this.productForm.name, this.productForm.url);
    if (!validation.isValid) {
      this.status = `Erreur : ${validation.message}`;
      return;
    }

    this.loading = true;
    this.status = 'Recherche du produit, extraction des informations et génération du site...';
    this.productService.scrapeProduct(this.productForm.name, this.productForm.url, userId).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate(['/preview']);
      },
      error: (err: any) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || 'Erreur de scraping.';
        this.status = 'Erreur : ' + message;
      },
    });
  }

  private generateFromImage(userId: number) {
    if (!this.selectedImage) {
      this.status = 'Erreur : Veuillez sélectionner une image.';
      return;
    }

    this.loading = true;
    this.status = 'Validation de l\'image en cours...';

    this.productService.validateProductImage(this.selectedImage, this.productForm.name).subscribe({
      next: (validationResult) => {
        if (!validationResult.isValid) {
          this.loading = false;
          this.status = 'Erreur : ' + (validationResult.message || 'L\'image ne correspond pas à un produit commercial.');
          return;
        }

        // Image validated, proceed with generation
        this.status = 'Analyse de l\'image, extraction des informations et génération du site...';
        this.productService.searchProductByImage(this.selectedImage!, this.productForm.name, userId, this.imagePreview).subscribe({
          next: () => {
            this.loading = false;
            this.router.navigate(['/preview']);
          },
          error: (err: any) => {
            this.loading = false;
            const message = err.error?.detail || err.error?.message || 'Erreur lors de l\'analyse de l\'image.';
            this.status = 'Erreur : ' + message;
          },
        });
      },
      error: (err: any) => {
        this.loading = false;
        const message = err?.message || 'Erreur lors de la validation de l\'image.';
        this.status = 'Erreur : ' + message;
      },
    });
  }
}
