# Rebuild Caddy v2.11.4 with the fixed Go toolchain and fixed Go modules.
FROM --platform=$BUILDPLATFORM golang:1.26.6-alpine3.24@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS caddy-build

ARG TARGETOS
ARG TARGETARCH
ARG CADDY_SOURCE_COMMIT=e2eee6a7fce366321294c9c2a79f3146891dcbdf

RUN apk add --no-cache git curl
RUN git clone --depth 1 --branch v2.11.4 https://github.com/caddyserver/caddy.git /src \
    && test "$(git -C /src rev-parse HEAD)" = "$CADDY_SOURCE_COMMIT"

WORKDIR /src
RUN go get golang.org/x/crypto@v0.55.0 golang.org/x/net@v0.57.0 golang.org/x/text@v0.41.0 google.golang.org/grpc@v1.82.1 \
    && go mod tidy \
    && go mod verify \
    && test "$(sha256sum go.mod | cut -d ' ' -f 1)" = a092f0a81444e26ecdea87d58858b3511737929cbeed4ab4d675153a8d3db51d \
    && test "$(sha256sum go.sum | cut -d ' ' -f 1)" = 5c04f692e2acc5bba059e6dfda5305d10acc087196c080ab2a006f364298cb24
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -buildvcs=false -mod=readonly -tags=nobadger,nomysql,nopgx \
      -ldflags='-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=v2.11.4-dudu.1' \
      -o /out/caddy ./cmd/caddy
RUN curl --fail --location --silent --show-error \
      --output /out/Caddyfile \
      https://raw.githubusercontent.com/caddyserver/dist/33ae08ff08d168572df2956ed14fbc4949880d94/config/Caddyfile \
    && printf '%s  %s\n' \
      66177d46fa761acb07208065db9b0274cb1b12c02ac43b9bfc9857b698b1ccfe \
      /out/Caddyfile | sha256sum -c - \
    && curl --fail --location --silent --show-error \
      --output /out/index.html \
      https://raw.githubusercontent.com/caddyserver/dist/33ae08ff08d168572df2956ed14fbc4949880d94/welcome/index.html \
    && printf '%s  %s\n' \
      70a45d667679109cd6b25502554597f21536ee995ada0549251167bd171533b9 \
      /out/index.html | sha256sum -c -

FROM alpine:3.24.1@sha256:e7a1a92a5bfeee40966aea60f0796b0e7917cc35591542701834f03a68fa3d18

RUN apk add --no-cache \
    ca-certificates=20260611-r0 \
    libcrypto3=3.5.8-r0 \
    libssl3=3.5.8-r0 \
    && install -d -m 1777 /config/caddy /data/caddy \
    && install -d /etc/caddy /usr/share/caddy
COPY --from=caddy-build /out/caddy /usr/bin/caddy
COPY --from=caddy-build /out/Caddyfile /etc/caddy/Caddyfile
COPY --from=caddy-build /out/index.html /usr/share/caddy/index.html

ENV CADDY_VERSION=v2.11.4-dudu.1 \
    XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data

LABEL org.opencontainers.image.version=v2.11.4-dudu.1 \
      org.opencontainers.image.title="DUDU hardened Caddy" \
      org.opencontainers.image.source="https://github.com/czeyik/Digital-Signage-Project"

EXPOSE 80
EXPOSE 443
EXPOSE 443/udp
EXPOSE 2019

WORKDIR /srv
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
