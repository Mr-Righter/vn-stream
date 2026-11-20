ARG NODE_IMAGE=registry-1.docker.io/library/node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9
ARG RUNTIME_IMAGE=ghcr.io/linuxserver/baseimage-selkies:debiantrixie@sha256:d5e900e9dbc7e0ec830758355cbb80f0b773ce55da82f59e7785298a510bc0f6
ARG RUST_IMAGE=registry-1.docker.io/library/rust:1.85.1-bookworm@sha256:e51d0265072d2d9d5d320f6a44dde6b9ef13653b035098febd68cce8fa7c0bc4
FROM ${NODE_IMAGE} AS web
WORKDIR /build
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/ ./
COPY src/vn_stream/i18n/locales/ /src/vn_stream/i18n/locales/
COPY src/vn_stream/ui/theme.json /src/vn_stream/ui/theme.json
RUN npm run check && npm run build

FROM ${RUST_IMAGE} AS asset-tools
ENV CARGO_HOME=/usr/local/cargo
WORKDIR /build
COPY tools/tlg-preview/ ./
RUN cargo build --release --locked && mkdir /licenses && \
    find /usr/local/cargo/registry/src -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' \) -exec cp --parents {} /licenses/ \;

FROM ${RUNTIME_IMAGE} AS python-package
WORKDIR /build
COPY LICENSE requirements.lock pyproject.toml ./
COPY src/ ./src/
RUN python3 -m venv /build/venv && \
    /build/venv/bin/pip install --no-cache-dir -c requirements.lock build setuptools wheel && \
    /build/venv/bin/python -m build --wheel --no-isolation --outdir /wheels

FROM ${RUNTIME_IMAGE}
ENV TITLE="VN Stream" \
    START_DOCKER=false \
    HARDEN_DESKTOP=true \
    SELKIES_ALLOWED_ORIGINS="" \
    NO_GAMEPAD=true \
    NO_STEAM=true \
    PIXELFLUX_WAYLAND=false \
    PULSE_SERVER=unix:/defaults/native \
    SDL_AUDIODRIVER=pulseaudio \
    PYTHONPATH=/opt/vn-stream/lib

WORKDIR /opt/vn-stream
COPY requirements.lock ./
COPY LICENSE /opt/vn-stream/licenses/VN-Stream.LICENSE
# Keep service dependencies separate from Selkies and game SDK interpreters.
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends unrar=1:7.1.8-1 ffmpeg=7:7.1.5-0+deb13u1 libsdl2-mixer-2.0-0=2.8.1+dfsg-2 libsdl2-ttf-2.0-0=2.24.0+dfsg-2 libsdl2-image-2.0-0=2.8.8+dfsg-1+deb13u1 liblua5.3-0=5.3.6-2+b4 libluajit-5.1-2=2.1.0+openresty20250117-2 libgomp1=14.2.0-19 libc++1-19=1:19.1.7-3+b1 libc++abi1-19=1:19.1.7-3+b1 libunwind-19=1:19.1.7-3+b1 libsdl1.2debian=1.2.68-3 libsdl-ttf2.0-0=2.0.11-6+b2 libsdl-image1.2=1.2.12-14 libsdl-mixer1.2=1.2.12-19+b1 libsmpeg0t64=0.4.5+cvs20030824-9.1+b1 libsndio7.0=1.10.0-0.1 && \
    rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/vn-stream/venv && \
    /opt/vn-stream/venv/bin/pip install --no-cache-dir -c requirements.lock fastapi uvicorn wsproto pyzipper py7zr rarfile pycryptodome pefile Pillow ecdsa
COPY --from=python-package /wheels/ /tmp/vn-wheels/
RUN /opt/vn-stream/venv/bin/pip install --no-deps --target /opt/vn-stream/lib /tmp/vn-wheels/*.whl && rm -rf /tmp/vn-wheels
COPY --from=web /build/dist/ ./web/
COPY --from=asset-tools /build/target/release/vn-tlg-preview /opt/vn-stream/bin/tlg-preview
COPY --from=asset-tools /licenses/ /opt/vn-stream/licenses/asset-tools/
COPY tools/tlg-preview/THIRD-PARTY-LICENSE tools/tlg-preview/Cargo.lock /opt/vn-stream/licenses/tlg-preview/
COPY containers/root/ /
COPY scripts/patch-selkies.py /opt/vn-stream/patch-selkies.py
RUN python3 /opt/vn-stream/patch-selkies.py
RUN chmod 755 /custom-cont-init.d/50-vn-stream /custom-services.d/vn-stream
ENV VN_STREAM_NATIVE_DISPLAY=selkies \
    RENPY_GL_VSYNC=0 \
    SELKIES_ENABLE_RESIZE=false \
    NO_DECOR=true \
    SELKIES_ENABLE_SHARING=false \
    SELKIES_MODE=websockets \
    SELKIES_ENABLE_DUAL_MODE=false
EXPOSE 3000 3001
VOLUME ["/config", "/data"]
