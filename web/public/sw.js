// Sheaf web push service worker.
//
// Minimal: receives push events and displays them as notifications. No
// caching, no background sync, no offline handling — keeps the surface as
// small as possible for what is fundamentally a notification-delivery
// channel and nothing else.

self.addEventListener("push", (event) => {
  let payload = { title: "Sheaf", body: "Front update" };
  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch {
      payload.body = event.data.text();
    }
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "/logo-light.svg",
      badge: "/logo-light.svg",
      tag: payload.tag || undefined,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  // Focus an existing tab if possible, else open the manage URL or home.
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          if ("focus" in client) return client.focus();
        }
        if (self.clients.openWindow) return self.clients.openWindow("/");
      }),
  );
});
