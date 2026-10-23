/**
 * Test environment (vitest + jsdom, Angular `@angular/build:unit-test`),
 * same runner as the fleet frontends (PushIT_frontend).
 *
 * The unit-test builder inherits the build target's polyfills, which only
 * list `zone.js`: `zone.js/testing` is what `fakeAsync`/`TestBed` need.
 */
import 'zone.js/testing';
