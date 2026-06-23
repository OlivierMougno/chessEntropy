# Image: lc0-policy-api
# Build: docker build -t lc0-policy-api .
# Run  : docker run -p 8000:8000 lc0-policy-api
#
# Compile Lc0 avec backend CPU "eigen" (portable, pas besoin de GPU).
# Pour un déploiement avec GPU CUDA, remplacer la base et `-Dbackend=cuda`.

FROM python:3.12-slim AS build-lc0

ARG LC0_REF=v0.31.2
# Réseau Maia 1900 (~25 Mo) : style humain, parfait pour démarrer.
# Remplace par un T-net officiel pour une force plus élevée.
ARG WEIGHTS_URL=https://github.com/CSSLab/maia-chess/releases/download/v1.0/maia-1900.pb.gz

RUN apt-get update && apt-get install -y --no-install-recommends \
      git build-essential ninja-build meson pkg-config \
      libeigen3-dev zlib1g-dev ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${LC0_REF} --recurse-submodules \
      https://github.com/LeelaChessZero/lc0.git

WORKDIR /src/lc0
RUN ./build.sh -Dgtest=false -Ddefault_backend=eigen

RUN curl -fL -o /weights.pb.gz "${WEIGHTS_URL}"

# ---------- runtime image ----------
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build-lc0 /src/lc0/build/release/lc0 /app/lc0
COPY --from=build-lc0 /weights.pb.gz /app/weights.pb.gz

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py

ENV LC0_PATH=/app/lc0 \
    LC0_WEIGHTS=/app/weights.pb.gz \
    LC0_BACKEND=eigen \
    LC0_NODES=1 \
    CORS_ORIGINS=*

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
