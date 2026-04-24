# SkyWatch — Idées d'améliorations v2

> Voir aussi `COMPETITIVE_ANALYSIS.md` pour l'analyse détaillée des projets similaires

## 🎯 Quick wins (1-2h, très impactants)
1. **Séismes temps réel** — USGS earthquake feed (gratuit, sans clé, très visuel avec pulsation)
2. **Filtres visuels GLSL** — CRT scanlines, night vision (vert phosphore), FLIR thermal (3 shaders PostProcess)
3. **Boot sequence militaire** — splash screen typewriter animation style terminal
4. **GPS Jamming overlay** — zones de brouillage GPS mondial (gpsjam.org, CSV quotidien)
5. **Pannes internet** — IODA API Georgia Tech (gratuit)
6. **Country labels** — noms de pays affichés sur le globe
7. **Altitude color bands** — couleur des avions selon altitude (bleu bas, jaune moyen, rouge haut)
8. **Crosshair/réticule** — overlay central style tactique
9. **Keyboard shortcuts** — 1-4 pour modes visuels, raccourcis layers
10. **Plus d'avions** — ajouter adsb.fi comme source complémentaire (×2 couverture)

## 🛠️ Améliorations moyennes (3-5h)
11. **Dead-reckoning** — animation smooth des avions entre les updates à 60fps
12. **Lock-on tracking** — verrouiller la caméra sur un avion/bateau (suit en temps réel, ESC pour déverrouiller)
13. **Intel feed** — fil d'événements temps réel dans le panel (séismes, alertes squawk, etc.)
14. **Route arcs** — arcs de trajectoire prédites pour les avions
15. **Playback/timeline** — slider pour remonter dans le temps
16. **Photos d'avions** — planespotters.net pour image de chaque avion au clic
17. **Filtres avancés** — par compagnie, type d'avion, altitude, type de bateau
18. **Mode plein écran** — masquer topbar + bottombar
19. **Alertes squawk** — notification si 7700/7600/7500
20. **Plus de webcams** — Caltrans, NYC DOT, autoroutes françaises

## 🚀 Ambitieux (1 jour+)
21. **AI news synthesis** — agréger flux RSS géopolitiques + synthétiser avec Perplexity
22. **Finance overlay** — données boursières par pays
23. **Country Intelligence Index** — score de risque composite par pays
24. **App desktop** — packager avec Tauri
25. **Performance primitives** — BillboardCollection pour 50K+ entités
26. **Architecture plugin** — chaque layer = module indépendant
27. **Feux de forêt** — NASA FIRMS (active fire data)
28. **Qualité de l'air** — overlay AQI mondial
29. **Courants marins** — animation des courants océaniques
30. **Vélos libre-service** — stations Vélib etc. via citybik.es

## 🆓 Sources de données gratuites découvertes
| Source | Données | Clé ? | URL |
|--------|---------|-------|-----|
| USGS | Séismes | Non | earthquake.usgs.gov/earthquakes/feed/ |
| GPSJam | Brouillage GPS | Non | gpsjam.org/data/ |
| IODA | Pannes internet | Non | api.ioda.inetintel.cc.gatech.edu |
| adsb.fi | Avions (complément) | Non | api.adsb.fi/v2/ |
| adsb.lol/v2/mil | Militaire spécifique | Non | api.adsb.lol/v2/mil |
| NASA FIRMS | Feux de forêt | Non | firms.modaps.eosdis.nasa.gov |
| Open-Meteo AQI | Qualité de l'air | Non | air-quality-api.open-meteo.com |
| citybik.es | Vélos libre-service | Non | api.citybik.es/v2/ |
| TxDOT | CCTV Texas | Non | its.txdot.gov |
| Caltrans | CCTV Californie | Non | cwwp2.dot.ca.gov |
| Wingbits | Avions (premium) | Oui | wingbits.com |
