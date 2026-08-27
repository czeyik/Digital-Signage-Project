# Retain the official PostgreSQL 16.15 Alpine entrypoint and replace only the
# Go-built gosu helper with a build from the same upstream source on fixed Go.
FROM --platform=$BUILDPLATFORM golang:1.26.6-alpine3.24@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83 AS gosu-build

ARG TARGETOS
ARG TARGETARCH
ARG GOSU_SOURCE_COMMIT=6456aaa0f3c854d199d0f037f068eb97515b7513

RUN apk add --no-cache git
RUN git clone --depth 1 --branch 1.19 https://github.com/tianon/gosu.git /src \
    && test "$(git -C /src rev-parse HEAD)" = "$GOSU_SOURCE_COMMIT"

WORKDIR /src
RUN go mod download \
    && go mod verify \
    && test "$(sha256sum go.mod | cut -d ' ' -f 1)" = 0475f1708db81d718b633faf2d9dd64695037eabdc8562125060607bcb01b2ba \
    && test "$(sha256sum go.sum | cut -d ' ' -f 1)" = 2a8f3fb6adb84839bbb9999f12f1416fb86c184135aaa1c2e489b35203c08346
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -buildvcs=false -mod=readonly -ldflags='-s -w' -o /out/gosu .

FROM postgres:16.15-alpine3.24@sha256:738d1359df5aa0b6d50a9071e989c49fdd39152a2a805c6ff131bf5e2243e0b3

RUN apk add --no-cache libcrypto3=3.5.8-r0 libssl3=3.5.8-r0

COPY --from=gosu-build /out/gosu /usr/local/bin/gosu
RUN chmod 0755 /usr/local/bin/gosu

LABEL org.opencontainers.image.title="DUDU hardened PostgreSQL 16.15" \
      org.opencontainers.image.source="https://github.com/czeyik/Digital-Signage-Project"
