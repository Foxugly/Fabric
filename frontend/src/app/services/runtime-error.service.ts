import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class RuntimeErrorService {
  private readonly errorSubject = new BehaviorSubject<string | null>(null);

  readonly error$ = this.errorSubject.asObservable();

  report(message: string): void {
    this.errorSubject.next(message);
    const overlay = globalThis.document.getElementById('fabric-runtime-error');
    if (overlay !== null) {
      overlay.textContent = message;
      overlay.style.display = 'block';
    }
  }

  clear(): void {
    this.errorSubject.next(null);
    const overlay = globalThis.document.getElementById('fabric-runtime-error');
    if (overlay !== null) {
      overlay.textContent = '';
      overlay.style.display = 'none';
    }
  }
}
