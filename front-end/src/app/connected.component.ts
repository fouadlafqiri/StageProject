import { Component } from '@angular/core';
import { RouterModule } from '@angular/router';
import { CommonModule } from '@angular/common';

import { ProductService } from '../product.service';

@Component({
  selector: 'app-connected',
  templateUrl: './connected.component.html',
  styleUrls: ['./connected.component.css'],
  standalone: true,
  imports: [CommonModule, RouterModule],
})
export class ConnectedComponent {
  constructor(private productService: ProductService) {}

  get user$() {
    return this.productService.user$;
  }
}
