FROM python:3.12-alpine AS pybuilder
ADD pyproject.toml pdm.lock /build/
WORKDIR /build
RUN apk add alpine-sdk python3-dev musl-dev linux-headers
RUN pip install pdm
RUN pdm install

FROM python:3.12-alpine AS runner
WORKDIR /app

RUN apk update && apk add --no-cache ffmpeg aria2 deno
COPY --from=pybuilder /build/.venv/lib/ /usr/local/lib/
RUN pip install --root-user-action=ignore -U --pre "yt-dlp[default]"
COPY src /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /data
WORKDIR /app

CMD ["/usr/local/bin/docker-entrypoint.sh"]
