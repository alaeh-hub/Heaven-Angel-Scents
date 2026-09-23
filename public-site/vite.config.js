import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Flask stays the backend: in dev (`npm run dev`, port 5173) the JSON
// API and the shared /static assets (style.css, images, videos,
// product photos) are proxied to Flask on :5000, so the React app runs
// same-origin with no CORS setup and the session cookie (which carries
// the CSRF token) just works.
//
// `npm run build` writes into ../static/public-site/, which Flask then
// serves — see routes/portal.py's _serve_public_site().
const FLASK = process.env.FLASK_URL || 'http://127.0.0.1:5000';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Only the build lives under Flask's /static; the dev server serves
  // from / so its own /static proxy below doesn't collide with it.
  base: command === 'build' ? '/static/public-site/' : '/',
  build: {
    outDir: '../static/public-site',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '^/partner-portal/[^/]+/api/.*': FLASK,
      '/static': FLASK,
    },
  },
}));
