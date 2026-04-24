# SkyWatch — Analyse concurrentielle et idées à copier

## Projets analysés (24 avril 2026)

### 1. WorldView (kevtoe) ⭐ Le plus proche de nous
**Stack:** React 19 + TypeScript + CesiumJS 1.138 + Vite + Tailwind + Express
**URL:** https://github.com/kevtoe/worldview | Demo: worldview.kt-o.com

**Ce qu'ils ont qu'on n'a pas:**
- 🎬 **Boot sequence militaire** — splash screen typewriter animation style terminal
- 🎨 **GLSL post-processing** — filtres CRT scanlines, night vision (vert phosphore), FLIR thermal
- ✈️ **Dead-reckoning** — positions extrapolées entre les updates à 60fps (smooth animation)
- 🎯 **Crosshair overlay** — réticule de visée au centre de l'écran
- 📊 **Intel feed** — fil d'événements temps réel (genre fil d'actualité des layers)
- 🔒 **Lock-on entity** — panel de suivi verrouillé sur une entité (ESC pour déverrouiller)
- ⚡ **Imperative Cesium primitives** — BillboardCollection, PointPrimitiveCollection pour 27K+ entités (perf)
- 🛩️ **27 000 avions** vs nos ~7500 (ils utilisent FlightRadar24 + adsb.fi)
- 📺 **CCTV multi-sources** — TfL + Austin TX + Transport NSW
- 🔄 **Route arcs** — arcs de trajectoire pour les avions
- 🏷️ **Altitude bands** — code couleur par tranche d'altitude

**Sources de données intéressantes:**
- adsb.fi (complément à adsb.lol pour plus d'avions)
- OpenStreetMap Overpass API pour le réseau routier

---

### 2. WorldView (receptor/alkeincodes) — "God's Eye"
**Stack:** React 19 + TypeScript + CesiumJS 1.139 + Vite + Zustand
**URL:** https://github.com/receptor/worldview

**Ce qu'ils ont qu'on n'a pas:**
- 🌍 **11 layers** vs nos ~10
- 🛡️ **GPS Jamming overlay** — données gpsjam.org (zones de brouillage GPS)
- 🔭 **Surveillance Passes** — passages de satellites de surveillance au-dessus de votre position  
- ✈️ **Airspace closures** — zones aériennes fermées/restreintes
- 🌐 **Internet outages** — pannes internet via IODA (Georgia Tech)
- ⏪ **Playback system** — remonter dans le temps avec un slider
- 🎖️ **Military HUD** — overlay tactique avec coordonnées MGRS, GSD estimation
- ⌨️ **Keyboard shortcuts** — raccourcis clavier pour les modes visuels et landmarks
- 🗺️ **Country labels** — noms de pays affichés sur le globe
- 💯 **100% open data** — aucune API payante

**Sources à copier:**
- gpsjam.org/data/ — brouillage GPS mondial (CSV gratuit)
- api.ioda.inetintel.cc.gatech.edu — pannes internet
- api.adsb.lol/v2/mil — endpoint militaire spécifique
- earthquake.usgs.gov/earthquakes/feed/ — séismes

---

### 3. WorldMonitor (koala73) — Le plus ambitieux
**Stack:** Vanilla TypeScript + Vite + globe.gl + deck.gl + Tauri 2
**URL:** https://github.com/koala73/worldmonitor | worldmonitor.app

**Ce qu'ils ont qu'on n'a pas:**
- 🤖 **AI-powered news** — agrégation 500+ flux RSS, synthétisés par IA en briefs
- 📊 **Country Intelligence Index** — score de risque composite (12 catégories)
- 💹 **Finance radar** — 92 bourses, commodités, crypto, composite 7 signaux
- 🌍 **45 data layers** (!!!)
- 🖥️ **App desktop native** (Tauri 2) — macOS, Windows, Linux
- 🌐 **21 langues** + flux natifs par langue + RTL
- 🔀 **5 variantes** depuis un seul codebase (world, tech, finance, commodity, happy)
- 🤖 **Local AI** — Ollama intégré, pas besoin de clé API
- 🗺️ **Dual map engine** — globe.gl (3D) ET deck.gl (WebGL flat)
- 📡 **65+ sources de données**
- ⚡ **Protocol Buffers** — 92 protos pour les contrats API
- 🔄 **3-tier cache** — Redis + CDN + Service Worker
- **Cross-stream correlation** — convergence de signaux militaires, économiques, catastrophes

**Leçons:**
- Un seul fichier HTML ne suffira pas pour atteindre ce niveau → migration vers une vraie app
- L'IA intégrée pour synthétiser les données est un GROS différenciateur
- Les multiples variantes (finance, tech) sont un concept malin

---

### 4. WorldWideView (silvertakana)
**Stack:** Next.js + CesiumJS + Plugin architecture
**URL:** https://github.com/silvertakana/worldwideview | worldwideview.dev

**Ce qu'ils ont qu'on n'a pas:**
- 🧩 **Architecture plugin** — chaque layer est un plugin indépendant (ES modules CDN)
- 🛒 **Plugin Marketplace** — communauté peut publier des layers
- ⚡ **100K+ objets** sans GPU stall (raw Cesium primitives)
- 🕸️ **Stack spiderification** — pour les emplacements denses
- 📡 **WebSocket Event Bus** — bus d'événements global pour la data
- 🐳 **Docker ready** — deployment en une commande
- 🔌 **CLI pour créer des plugins** : `npx @worldwideview/create-plugin my-layer`

**Leçons:**
- L'architecture plugin est brillante pour scaler
- Le bus d'événements WebSocket centralise bien la data

---

## 🎯 Top 20 fonctionnalités à copier (priorité)

### Facile et impactant (1-2h chacune)
1. **Séismes temps réel** — USGS feed, gratuit, sans clé, très visuel
2. **GPS Jamming overlay** — gpsjam.org, CSV quotidien
3. **Pannes internet** — IODA API, gratuit
4. **Filtres visuels GLSL** — CRT scanlines, night vision, FLIR (3 shaders)
5. **Boot sequence** — splash screen militaire animé
6. **Country labels** — noms de pays sur le globe
7. **Keyboard shortcuts** — raccourcis pour les layers et navigation
8. **Altitude color bands** — couleur des avions selon altitude
9. **Plus d'avions** — ajouter adsb.fi comme source complémentaire
10. **Crosshair/réticule** — overlay central style tactique

### Moyen (3-5h)
11. **Intel feed** — fil d'événements temps réel dans le panel
12. **Dead-reckoning** — animation smooth des avions entre les updates
13. **Lock-on tracking** — verrouiller la caméra sur une entité
14. **Playback/timeline** — slider pour remonter dans le temps
15. **Route arcs** — arcs de trajectoire prédites pour les avions

### Ambitieux (1 jour+)
16. **AI news synthesis** — agréger des flux RSS et synthétiser avec Perplexity/GPT
17. **Finance overlay** — données boursières sur la carte (par pays)
18. **Architecture plugin** — refactorer en modules indépendants
19. **App desktop** — packager avec Tauri/Electron
20. **Performance primitives** — migrer vers BillboardCollection pour supporter 50K+ entités
