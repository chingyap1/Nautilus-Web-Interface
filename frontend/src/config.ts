// API Configuration
// Vite development talks directly to the local backend ports. Production builds
// use same-origin paths so nginx can route browser traffic to Docker services.

const productionWsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

export const API_CONFIG = {
  NAUTILUS_API_URL:
    import.meta.env.VITE_NAUTILUS_API_URL || (import.meta.env.PROD ? '' : 'http://localhost:8000'),
  ADMIN_DB_API_URL:
    import.meta.env.VITE_ADMIN_DB_API_URL || (import.meta.env.PROD ? '/admin-api' : 'http://localhost:8001'),
  WS_URL:
    import.meta.env.VITE_WS_URL || (import.meta.env.PROD ? productionWsUrl : 'ws://localhost:8000'),
  TIMEOUT: 30000, // 30 seconds (backtest can take time)
};

export async function loadApiConfig(): Promise<void> {
  // no-op: config is resolved from environment variables at build time
}

export default API_CONFIG;

