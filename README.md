# MangaDownloader

Projet perso vibecode — aucune garantie, aucun support, ça marche sur ma machine. Je suis sûr de rien techniquement, j'ai juste fait ce qui marchait de mon côté.

Télécharge les chapitres/volumes de MangaFire et MangaPlus en images et les archive en `.zip`.

---

## Ce que ça fait

### MangaFire
1. Tu colles une URL MangaFire (ex: `https://mangafire.to/read/madd.90658/fr/volume-1`)
2. FlareSolverr bypass Cloudflare et récupère les cookies de session (optionnel)
3. Zen Browser s'ouvre avec les cookies injectés
4. Tu charges les images manuellement dans le navigateur
5. Tu appuies sur Entrée — le script vérifie que toutes les pages sont là
6. Téléchargement avec retry automatique si le CDN bug (erreurs 520)
7. Archive `.zip` créée dans `download/`

### MangaPlus
1. Tu colles une URL MangaPlus (ex: `https://mangaplus.shueisha.co.jp/viewer/7002474`)
2. Zen Browser s'ouvre sur le viewer
3. Le script navigue automatiquement page par page et capture les images (blob URLs)
4. Archive `.zip` créée dans `download/`

---

## Prérequis

- **Python 3.11+**
- **Zen Browser** installé *(Firefox-based — Chrome/Brave se font rediriger par Cloudflare à cause du TLS fingerprint)*
- **FlareSolverr** qui tourne sur le réseau local (MangaFire uniquement, optionnel)
- **geckodriver** géré automatiquement via `webdriver-manager`

### Install deps

```bash
pip install selenium requests webdriver-manager
```

### FlareSolverr (MangaFire uniquement)

Le script propose d'utiliser FlareSolverr au démarrage (y/n). Si tu réponds `n`, le navigateur s'ouvre directement sans bypass. FlareSolverr doit tourner avant de lancer le script si tu réponds `y`.

```bash
# Docker (exemple)
docker run -d -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
```

---

## Utilisation

```bash
python manga_downloader.py
```

Le script demande une URL au démarrage. Colle une URL MangaFire ou MangaPlus.

---

## Variables d'environnement

| Variable | Défaut | Description |
|---|---|---|
| `ZEN_BINARY` | auto-détecté | Chemin vers `zen.exe` si non standard |
| `FLARESOLVERR_URL` | `http://127.0.0.1:8191/v1` | Endpoint FlareSolverr |
| `USE_FLARESOLVERR` | `1` | Mettre `0` pour désactiver |
| `HEADLESS` | `0` | Mettre `1` pour mode headless (déconseillé) |
| `DEBUG_DOM` | `1` | Mettre `0` pour désactiver les fichiers debug HTML |

---

## Pages manquantes

Si le CDN de MangaFire est en carafe (erreur 520), les pages échouées après 5 tentatives sont remplacées par un **fichier vide sans extension** avec le numéro de page comme nom (ex: `003`). Le zip est quand même créé avec les pages disponibles.

---

## Pourquoi Zen Browser et pas Chrome/Brave

Testé — Brave et Chrome se font systématiquement rediriger vers l'accueil par Cloudflare.

La raison : Cloudflare identifie les navigateurs via le **TLS fingerprint (JA3/JA4)**, une empreinte générée lors du handshake HTTPS, avant même que JavaScript tourne. Chromium (Chrome, Brave, Edge) a une empreinte très connue et massivement ciblée par les systèmes anti-bot. Même avec tous les flags anti-détection (`navigator.webdriver = false`, etc.), le TLS handshake trahit Chromium.

Firefox a une empreinte TLS différente, moins associée aux bots. Zen Browser est basé sur Firefox → hérite de son fingerprint → passe sous le radar de Cloudflare.

---

## Limites connues

- MangaFire et MangaPlus seulement
- Cloudflare change régulièrement — si ça casse, c'est la vie
- MangaFire : les images doivent être chargées manuellement dans le navigateur
- MangaPlus : navigation automatique page par page, peut rater des pages si le viewer lag
- Projet vibecode : le code est fonctionnel, pas propre

---

## Licence

Aucune. Fais-en ce que tu veux, mais ne te plains pas si ça casse.
