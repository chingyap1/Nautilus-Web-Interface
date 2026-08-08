import api from '@/lib/api';

export type PushStatus = {
  available: boolean;
  reason: string | null;
  subscribed: boolean;
  subscription_count: number;
};

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) {
    output[i] = raw.charCodeAt(i);
  }
  return output;
}

export async function fetchPushStatus(): Promise<PushStatus> {
  return api.get<PushStatus>('/api/push/status');
}

export async function fetchVapidPublicKey(): Promise<string> {
  const data = await api.get<{ public_key: string }>('/api/push/vapid-public-key');
  return data.public_key;
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export async function ensurePushServiceWorker(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.register('/sw.js');
}

export async function enablePushNotifications(): Promise<PushStatus> {
  if (!pushSupported()) {
    throw new Error('Web Push is not supported in this browser');
  }
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('Notification permission was not granted');
  }
  const registration = await ensurePushServiceWorker();
  await navigator.serviceWorker.ready;
  const publicKey = await fetchVapidPublicKey();
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
  });
  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error('Push subscription missing required keys');
  }
  await api.post('/api/push/subscribe', {
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
  });
  return fetchPushStatus();
}

export async function disablePushNotifications(): Promise<PushStatus> {
  if (pushSupported()) {
    const registration = await navigator.serviceWorker.getRegistration('/sw.js');
    const existing = await registration?.pushManager.getSubscription();
    if (existing) {
      const endpoint = existing.endpoint;
      try {
        await existing.unsubscribe();
      } catch {
        /* still drop server row */
      }
      await api.post('/api/push/unsubscribe', { endpoint });
      return fetchPushStatus();
    }
  }
  return fetchPushStatus();
}
