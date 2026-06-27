import { Component } from '@angular/core';
import { Router, RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ProductService } from '../product.service';

@Component({
  selector: 'app-auth',
  templateUrl: './auth.component.html',
  styleUrls: ['./auth.component.css'],
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
})
export class AuthComponent {
  authTab: 'login' | 'register' = 'login';
  authForm = { email: '', motDePasse: '', nom: '' };
  loading = false;
  status = '';

  constructor(private productService: ProductService, private router: Router) {}

  switchTab(tab: 'login' | 'register') {
    this.authTab = tab;
    this.status = '';
    this.loading = false;
  }

  onLogin() {
    this.loading = true;
    this.status = 'Connexion en cours...';

    this.productService.login({ email: this.authForm.email, motDePasse: this.authForm.motDePasse }).subscribe({
      next: () => {
        this.loading = false;
        this.status = 'Connecté !';
        this.router.navigate(['/']);
      },
      error: (err: any) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || err.message || 'Impossible de se connecter.';
        this.status = 'Erreur : ' + message;
      },
    });
  }

  onRegister() {
    this.loading = true;
    this.status = 'Création du compte...';

    this.productService.register(this.authForm).subscribe({
      next: () => {
        this.loading = false;
        this.status = 'Compte créé !';
        this.router.navigate(['/']);
      },
      error: (err: any) => {
        this.loading = false;
        const message = err.error?.detail || err.error?.message || err.message || 'Impossible de créer le compte.';
        this.status = 'Erreur : ' + message;
      },
    });
  }
}
