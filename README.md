# Pulso emocional de la música en México

Flujo de datos de principio a fin que cruza, para cada canción del top semanal de Spotify México:

- **cuánto se escucha:** Spotify Charts, CSV semanal;
- **qué dice la letra:** LRCLIB, API de letras;
- **cómo reacciona el público:** comentarios del video oficial en YouTube.

Sobre las letras y los comentarios calcula el sentimiento y la emoción, y los entrega en un dashboard y un modelo simple para el área de marketing de un sello discográfico.

**Estado:** diseño. El RFC está en [docs/rfc.md](docs/rfc.md) y el diagrama del flujo en [docs/diagrama_flujo.png](docs/diagrama_flujo.png). El código se construye a partir del 20 de noviembre de 2026.

## Datos que no están en el repo

- **Los CSV de Spotify Charts no se incluyen**, porque los términos de Spotify prohíben redistribuirlos. Cada quien los descarga con el botón oficial de *Weekly Top Songs Mexico* en charts.spotify.com y los deja en `data/landing/charts/`.
- **El archivo `.env` no se sube.** Hay que crearlo a partir de `.env.example`, con una API key propia de YouTube Data API v3.
