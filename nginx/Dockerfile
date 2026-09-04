# The config is baked in rather than bind-mounted.
#
# Mounted, nginx reads it once at startup and a later edit changes nothing
# until the container is recreated - which "docker compose up -d" will not do,
# because the service definition is identical and only the mounted file moved.
# A deploy would then silently keep serving the old config. Built in, any
# change to default.conf produces a new image and therefore a new container.
FROM nginx:1.27-alpine
COPY default.conf /etc/nginx/conf.d/default.conf
