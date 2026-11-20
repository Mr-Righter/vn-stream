ARG BUILD_IMAGE=ghcr.io/linuxserver/baseimage-selkies:debiantrixie@sha256:d5e900e9dbc7e0ec830758355cbb80f0b773ce55da82f59e7785298a510bc0f6
FROM ${BUILD_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends build-essential bison flex gperf gettext libudev-dev libpulse-dev libxkbcommon-dev libx11-xcb-dev libxcb-xkb-dev libwayland-dev wayland-protocols libegl1-mesa-dev cmake ninja-build git curl zip unzip tar pkg-config libsdl2-dev libsdl2-ttf-dev libsdl2-image-dev libsdl2-mixer-dev libbz2-dev libjpeg-dev liblua5.3-dev libgles2-mesa-dev libgtk-3-dev libxinerama-dev libxcursor-dev libxi-dev libxrandr-dev libgl1-mesa-dev libglu1-mesa-dev libtool libltdl-dev autoconf autoconf-archive automake nasm yasm python3-jinja2 && ln -s /usr/lib/x86_64-linux-gnu/liblua5.3.so /usr/lib/x86_64-linux-gnu/liblua.so && rm -rf /var/lib/apt/lists/*

# SDL 1.2 ABI uses Debian's maintained SDL2 compatibility implementation.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsdl1.2-compat-dev=1.2.68-3 libsdl-ttf2.0-dev=2.0.11-6+b2 \
    libsdl-image1.2-dev=1.2.12-14 libsdl-mixer1.2-dev=1.2.12-19+b1 \
    libsmpeg-dev=0.4.5+cvs20030824-9.1+b1 libmad0-dev=0.15.1b-11+b1 \
    && rm -rf /var/lib/apt/lists/*

# ONScripter-zero uses system libraries through the checked-in CMake overlay.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfmt-dev=10.1.1+ds1-4 libluajit-5.1-dev=2.1.0+openresty20250117-2 \
    libstb-dev=0.0~git20241109.5c20573+ds-1 \
    libfreetype-dev=2.13.3+dfsg-1+deb13u1 libharfbuzz-dev=10.2.0-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/*

# NEW builds its pinned static SDL3/FFmpeg dependency recipes.
RUN apt-get update && apt-get install -y --no-install-recommends meson=1.7.0-1 python3-setuptools=78.1.1-0.1 libdrm-dev=2.4.124-2 libgbm-dev=25.0.7-2+deb13u1 libsndio-dev=1.10.0-0.1 && rm -rf /var/lib/apt/lists/*
