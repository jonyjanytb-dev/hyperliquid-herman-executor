const CACHE="herman-gateway-v1";
const SHELL=["/","/manifest.webmanifest"];
self.addEventListener("install",e=>e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL))));
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{
  const u=new URL(e.request.url);
  if(u.pathname.startsWith("/api/")||u.pathname==="/healthz") return;
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});
