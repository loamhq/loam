# loam-ui. One container, one port, no npm dependencies to install.
#
#   docker build -t loam-ui .
#   docker run --rm -p 8080:8080 --read-only -v loam-ui-storage:/storage \
#     -e BROKER_URL=http://<broker-host>:<port> -e BROKER_TOKEN=<token> loam-ui
#
# The app is Node's standard library and nothing else, so there is no npm
# install, no lockfile and no build: the image is the base, one system package
# and this repo's own files. The host builds it from a named commit and puts its own proxy and
# certificate in front; this container knows nothing of any domain.

FROM node:24-alpine

ENV NODE_ENV=production \
    APP_PORT=8080

# The one system package: `ffmpeg`, which turns the speech model's WAV into
# the MP3 a reply is read aloud from (src/speech.js). Alpine's build carries
# libmp3lame. Without it the app still runs and speech says "no encoder".
RUN apk add --no-cache ffmpeg

WORKDIR /app

COPY package.json ./
COPY src ./src
COPY public ./public
COPY tools/measure-speech.mjs ./tools/measure-speech.mjs

# Durable state has one home. What this app keeps there is the read-aloud
# audio under /storage/speech — ephemeral, swept 48 hours after its last play,
# and nothing that is not made again on asking. The directory is owned by the
# user the app runs as: a named volume mounted over it inherits that ownership,
# and a first write on a fresh volume would otherwise fail on a root-owned root.
# With a read-only root and no volume here, speech says "no storage" and the
# rest of the app is unaffected.
RUN mkdir -p /storage && chown node:node /storage

USER node

EXPOSE 8080

# The one endpoint anything supervising this app reads. It answers when this
# app can serve; it is not a claim about the broker, which may be down while
# the page is perfectly able to say so.
HEALTHCHECK --interval=30s --timeout=3s --start-period=3s --retries=3 \
  CMD wget -q --spider "http://127.0.0.1:${APP_PORT}/up" || exit 1

CMD ["node", "src/server.js"]
