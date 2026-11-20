ARG APP_IMAGE=vn-stream:local
FROM ${APP_IMAGE}
COPY --chown=0:0 runtimes/ /opt/vn-stream/bundled-runtimes/
COPY --chown=0:0 licenses/ /opt/vn-stream/licenses/runtimes/
COPY --chown=0:0 provenance.json /opt/vn-stream/runtime-provenance.json
ENV VN_STREAM_BUNDLED_RUNTIMES=/opt/vn-stream/bundled-runtimes
