import { provideHttpClient, withXhr } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';

import { Agent } from '../models';
import { AgentService } from './agent.service';
import { ApiBaseService } from './api-base.service';
import { AuthService } from './auth.service';

describe('AgentService.agents$', () => {
  let httpMock: HttpTestingController;
  let service: AgentService;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withXhr()),
        provideHttpClientTesting(),
        {
          provide: ApiBaseService,
          useValue: { baseUrl: '/api/v1', buildUrl: (path: string) => `/api/v1${path}` },
        },
        { provide: AuthService, useValue: { session$: new BehaviorSubject({ user: {} }) } },
      ],
    });
    httpMock = TestBed.inject(HttpTestingController);
    service = TestBed.inject(AgentService);
  });

  afterEach(() => {
    httpMock.verify();
    TestBed.resetTestingModule();
    vi.useRealTimers();
  });

  it('keeps polling after a failed request instead of erroring out', () => {
    const received: Agent[][] = [];
    const errors: unknown[] = [];
    const subscription = service.agents$.subscribe({
      next: (agents) => received.push(agents),
      error: (error: unknown) => errors.push(error),
    });

    vi.advanceTimersByTime(0);
    httpMock
      .expectOne('/api/v1/agents/')
      .flush(null, { status: 502, statusText: 'Bad Gateway' });

    vi.advanceTimersByTime(5000);
    httpMock.expectOne('/api/v1/agents/').flush([]);

    expect(errors).toEqual([]);
    expect(received).toEqual([[]]);
    subscription.unsubscribe();
  });
});
