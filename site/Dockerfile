FROM nginx:alpine
COPY public/ /usr/share/nginx/html/
# The stock entrypoint renders /etc/nginx/templates/*.template into
# /etc/nginx/conf.d/ with envsubst, so ${SITE_TOKEN} is filled at start.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY require-site-token.sh /docker-entrypoint.d/05-require-site-token.sh
EXPOSE 8080
