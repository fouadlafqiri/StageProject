import { NgModule } from '@angular/core';
import { RouterModule, Routes } from '@angular/router';

import { ConnectedComponent } from './connected.component';
import { ProductGeneratorPageComponent } from './pages/product-generator/product-generator.page';
import { ProductPreviewPageComponent } from './pages/product-preview/product-preview.page';
import { AuthComponent } from './auth.component';
import { ResultsComponent } from './results.component';

const routes: Routes = [
  { path: '', component: ProductGeneratorPageComponent },
  { path: 'login', component: AuthComponent },
  { path: 'connected', component: ConnectedComponent },
  { path: 'preview', component: ProductPreviewPageComponent },
  { path: 'resultats', component: ResultsComponent },
  { path: '**', redirectTo: '' }
];


@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule { }
