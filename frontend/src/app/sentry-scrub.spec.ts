import type { Event } from '@sentry/angular';

import { FILTERED, scrubBreadcrumb, scrubEvent, scrubText } from './sentry-scrub';

const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoxfQ.c2lnbmF0dXJl';

describe('sentry scrubbing', () => {
  it('masks the WebSocket ticket and token parameters', () => {
    expect(scrubText('wss://h/ws/v1/events/?ticket=abc123&x=1')).toBe(
      `wss://h/ws/v1/events/?ticket=${FILTERED}&x=1`,
    );
    expect(scrubText('/x?token=t&refresh=r')).toBe(`/x?token=${FILTERED}&refresh=${FILTERED}`);
  });

  it('masks JWTs and Bearer values anywhere in a string', () => {
    expect(scrubText(`failed with ${JWT}`)).toBe(`failed with ${FILTERED}`);
    expect(scrubText('Authorization: Bearer abc.def')).toBe(`Authorization: Bearer ${FILTERED}`);
  });

  it('scrubs an error event end to end', () => {
    const event: Event = {
      message: `boom ${JWT}`,
      request: {
        url: 'https://fabric.foxugly.com/ws/v1/events/?ticket=secret',
        headers: { Authorization: `Bearer ${JWT}`, 'User-Agent': 'ua' },
        cookies: { fabric_refresh: 'r' },
        data: { command: 'Get-Content .env' },
      },
      extra: { stdout: 'API_KEY=123' },
      exception: { values: [{ type: 'HttpErrorResponse', value: `401 for ${JWT}` }] },
      breadcrumbs: [
        { category: 'console', message: 'terminal output' },
        { category: 'xhr', data: { url: '/api/v1/auth/ws-ticket/?ticket=t' } },
      ],
    };

    const scrubbed = scrubEvent(event);
    const serialized = JSON.stringify(scrubbed);

    expect(serialized).not.toContain(JWT);
    expect(serialized).not.toContain('secret');
    expect(serialized).not.toContain('Get-Content');
    expect(serialized).not.toContain('API_KEY');
    expect(serialized).not.toContain('terminal output');
    expect(scrubbed.request?.headers?.['Authorization']).toBe(FILTERED);
    expect(scrubbed.request?.headers?.['User-Agent']).toBe('ua');
    expect(scrubbed.breadcrumbs).toHaveLength(1);
  });

  it('drops console breadcrumbs', () => {
    expect(scrubBreadcrumb({ category: 'console', message: 'x' })).toBeNull();
  });
});
