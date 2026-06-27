import { Component } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import type { Observable } from 'rxjs';

import { ProductService } from '../product.service';

type User = { nom?: string; email?: string; id?: number };

@Component({
  selector: 'app-home',
  templateUrl: './home.component.html',
  styleUrls: ['./home.component.css'],
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
})
export class HomeComponent {
  user$: Observable<User | null> = null as any;
  authTab: 'login' | 'register' = 'login';

  productForm = { name: '', url: '', template: 'minimal' };
  authForm = { email: '', motDePasse: '', nom: '' };

  loading = false;
  status = '';

  constructor(private productService: ProductService, private router: Router) {
    this.user$ = this.productService.user$;
  }

  switchTab(tab: 'login' | 'register') {
    this.authTab = tab;
    this.status = '';
    this.loading = false;
  }

  scrollToGenerator() {
    document.getElementById('generatorPanel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  onLogin() {
    this.loading = true;
    this.status = 'Connexion en cours...';

    this.productService.login(this.authForm).subscribe({
      next: () => {
        this.loading = false;
        this.status = 'Connecté !';
        this.router.navigate(['/connected']);
      },
      error: (err) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || err.message || 'Impossible de se connecter.';
        this.status = 'Erreur : ' + message;
      }
    });
  }

  onRegister() {
    this.loading = true;
    this.status = 'Création du compte...';

    this.productService.register(this.authForm).subscribe({
      next: () => {
        this.loading = false;
        this.status = 'Compte créé !';
        this.router.navigate(['/connected']);
      },
      error: (err) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || err.message || 'Impossible de créer le compte.';
        this.status = 'Erreur : ' + message;
      }
    });
  }

  onLogout() {
    this.productService.logout();
  }

  generate() {
    const user = this.productService.userSubject.value;
    if (!user?.id) return;

    const validation = this.productService.validateProductNameUrl(this.productForm.name, this.productForm.url);
    if (!validation.isValid) {
      this.status = `Erreur : ${validation.message}`;
      return;
    }

    this.loading = true;
    this.status = 'Recherche du produit, extraction des informations et génération du site...';

    this.productService.scrapeProduct(this.productForm.name, this.productForm.url, user.id).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate(['/preview']);
      },
      error: (err) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || err.message || 'Erreur de scraping.';
        this.status = 'Erreur : ' + message;
      }
    });
  }
}

